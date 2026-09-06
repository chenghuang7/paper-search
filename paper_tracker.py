"""Dependency-free arXiv tracker. Python 3.9+, UTC storage, static output."""
import argparse
import copy
import hashlib
import html
import json
import logging
import re
import shutil
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ATOM = {"a": "http://www.w3.org/2005/Atom", "o": "http://a9.com/-/spec/opensearch/1.1/"}
LOG = logging.getLogger("paper_tracker")


def stamp(value):
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def date(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def load_config(path):
    config = read_json(path)
    ids = set()
    if not config.get("topics"):
        raise ValueError("topics 不能为空")
    for topic in config["topics"]:
        if not re.fullmatch(r"[a-z0-9-]+", topic["id"]) or topic["id"] in ids:
            raise ValueError("主题 id 必须唯一且仅包含小写字母、数字和连字符")
        ids.add(topic["id"])
        if not topic.get("name") or not topic.get("groups") or any(not group for group in topic["groups"]):
            raise ValueError("主题必须包含名称及非空关键词组")
        for term in [term for group in topic["groups"] for term in group] + topic.get("exclude", []):
            if not isinstance(term, str) or not term.strip() or any(c in term for c in '\"\\\n\r'):
                raise ValueError("关键词必须是非空普通文本，不能包含引号、反斜线或换行")
    for key in ("initial_days", "overlap_days"):
        if type(config.get(key)) is not int or config[key] < 1:
            raise ValueError(key + " 必须是正整数")
    return config


def normalize(text):
    return re.sub(r"\s+", " ", re.sub(r"[-‐‑–—]", " ", text.casefold())).strip()


def contains(text, term):
    return re.search(r"(?<!\w)" + re.escape(normalize(term)) + r"(?!\w)", text) is not None


def matches(paper, topic):
    text = normalize(paper["title"] + " " + paper["abstract"])
    return (all(any(contains(text, term) for term in group) for group in topic["groups"])
            and not any(contains(text, term) for term in topic.get("exclude", [])))


def make_query(topic):
    groups = ["(" + " OR ".join('(ti:"{0}" OR abs:"{0}")'.format(term.strip()) for term in group) + ")"
              for group in topic["groups"]]
    # arXiv supports submittedDate filtering, but no lastUpdatedDate filter.
    # Sort updates descending and stop locally at the checkpoint instead.
    return " AND ".join(groups)


class ArxivClient:
    def __init__(self, opener=urllib.request.urlopen, sleep=time.sleep):
        self.opener = opener
        self.sleep = sleep
        self.last_request = None

    def request(self, url):
        for attempt in range(3):
            if self.last_request is not None:
                self.sleep(max(0, 3.1 - (time.monotonic() - self.last_request)))
            self.last_request = time.monotonic()
            try:
                request = urllib.request.Request(url, headers={"User-Agent": "TimeSeriesPaperTracker/1.0 (personal academic digest)"})
                with self.opener(request, timeout=45) as response:
                    return response.read()
            except Exception as error:
                if attempt == 2:
                    raise
                LOG.warning("请求失败，将重试 (%d/3): %s", attempt + 1, error)
                self.sleep(2 ** (attempt + 1))

    def fetch(self, topic, start, end):
        offset, expected = 0, None
        previous_updated = None
        seen = set()
        while True:
            params = {"search_query": make_query(topic), "start": offset,
                      "max_results": 100, "sortBy": "lastUpdatedDate", "sortOrder": "descending"}
            payload = self.request("https://export.arxiv.org/api/query?" + urllib.parse.urlencode(params))
            feed = ET.fromstring(payload)
            total_text = feed.findtext("o:totalResults", namespaces=ATOM)
            if total_text is None:
                raise ValueError("arXiv 未返回有效分页信息")
            total = int(total_text)
            if expected is not None and total != expected:
                raise ValueError("检索期间结果集发生变化，请重试")
            expected = total
            LOG.info("%s：读取第 %d 页（检索总量 %d，按时间窗口截取）", topic["name"], offset // 100 + 1, total)
            if int(feed.findtext("o:startIndex", default=str(offset), namespaces=ATOM)) != offset:
                raise ValueError("arXiv 分页位置异常")
            entries = feed.findall("a:entry", ATOM)
            if not entries and offset < total:
                raise ValueError("arXiv 返回不完整分页")
            for entry in entries:
                def field(name):
                    value = entry.findtext("a:" + name, namespaces=ATOM)
                    if not value:
                        raise ValueError("arXiv 论文缺少字段: " + name)
                    return " ".join(value.split())
                raw_id = field("id").split("/abs/")[-1]
                if not re.fullmatch(r"(?:\d{4}\.\d{4,5}|[a-zA-Z.-]+/\d{7})(?:v\d+)?", raw_id):
                    raise ValueError("arXiv 返回错误条目或无效 ID")
                paper_id = re.sub(r"v\d+$", "", raw_id)
                if paper_id in seen:
                    raise ValueError("arXiv 分页出现重复，未保存不完整结果")
                seen.add(paper_id)
                published, updated = field("published"), field("updated")
                date(published)
                updated_date = date(updated)
                if previous_updated is not None and updated_date > previous_updated:
                    raise ValueError("arXiv 返回的更新时间排序异常")
                previous_updated = updated_date
                if updated_date < start:
                    return
                if updated_date > end:
                    continue
                yield {"id": paper_id, "title": field("title"), "abstract": field("summary"),
                       "authors": [author.findtext("a:name", default="", namespaces=ATOM) for author in entry.findall("a:author", ATOM)],
                       "published": published, "updated": updated,
                       "url": "https://arxiv.org/abs/" + paper_id, "pdf_url": "https://arxiv.org/pdf/" + paper_id}
            offset += len(entries)
            if offset >= total:
                break
            if offset >= 30000:
                raise ValueError("已达到 arXiv 30000 条分页上限，未保存部分结果；请收窄检索主题")


def sync(config, previous, client, now):
    state = copy.deepcopy(previous)
    fingerprint = hashlib.sha256(json.dumps(config["topics"], sort_keys=True).encode()).hexdigest()
    changed = fingerprint != previous.get("config_fingerprint")
    start = now - timedelta(days=config["initial_days"])
    if previous.get("last_success"):
        catchup = date(previous["last_success"]) - timedelta(days=config["overlap_days"])
        start = min(start, catchup) if changed else catchup
    papers = {paper["id"]: paper for paper in state["papers"]}
    new_ids = set()
    for topic in config["topics"]:
        LOG.info("检索 %s，窗口 %s 至 %s", topic["name"], stamp(start), stamp(now))
        for incoming in client.fetch(topic, start, now):
            if not matches(incoming, topic):
                continue
            old = papers.get(incoming["id"])
            # Do not populate a fresh installation with ancient papers revised today.
            if old is None and date(incoming["published"]) < start:
                continue
            if old is None:
                new_ids.add(incoming["id"])
            if old is None or date(incoming["updated"]) >= date(old["updated"]):
                incoming = dict(incoming, first_seen=old["first_seen"] if old else stamp(now))
                papers[incoming["id"]] = incoming
    for paper in papers.values():
        paper["topics"] = [topic["id"] for topic in config["topics"] if matches(paper, topic)]
    state.update(papers=sorted(papers.values(), key=lambda p: (p["published"], p["id"]), reverse=True),
                 last_success=stamp(now), last_attempt=stamp(now), last_new_ids=sorted(new_ids),
                 error=None, config_fingerprint=fingerprint)
    return state


def build(config, state, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    for asset in ("style.css", "app.js"):
        shutil.copyfile(ROOT / "web" / asset, output / asset)
    data = dict(state, title=config["title"], topics=[{"id": t["id"], "name": t["name"]} for t in config["topics"]])
    serialized = json.dumps(data, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    template = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    (output / "index.html").write_text(template.replace("{{TITLE}}", html.escape(config["title"])).replace("{{DATA}}", serialized), encoding="utf-8")
    (output / ".nojekyll").touch()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["update", "build"])
    parser.add_argument("--config", type=Path, default=ROOT / "config.json")
    parser.add_argument("--data", type=Path, default=ROOT / "data/papers.json")
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    config, state = load_config(args.config), read_json(args.data)
    failed = False
    if args.command == "update":
        now = datetime.now(timezone.utc)
        try:
            state = sync(config, state, ArxivClient(), now)
            LOG.info("完成检索：新增 %d 篇，共 %d 篇", len(state["last_new_ids"]), len(state["papers"]))
        except Exception as error:
            LOG.error("检索失败，保留历史数据: %s", error)
            state = dict(state, last_attempt=stamp(now), error="本次检索失败，已保留上次结果；下次运行将自动补抓。")
            failed = True
        write_json(args.data, state)
    build(config, state, args.output)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
