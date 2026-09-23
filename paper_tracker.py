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
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ATOM = {"a": "http://www.w3.org/2005/Atom", "o": "http://a9.com/-/spec/opensearch/1.1/"}
OAI = {"o": "http://www.openarchives.org/OAI/2.0/", "r": "http://arxiv.org/OAI/arXivRaw/"}
LOG = logging.getLogger("paper_tracker")
REQUEST_INTERVAL = 31.0
ARXIV_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)


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
    if config.get("source", "api") not in ("api", "oai"):
        raise ValueError("source 必须为 api 或 oai")
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


def make_query(topic, candidate_only=False):
    keyword_groups = topic["groups"]
    if candidate_only:
        # Every match must satisfy every group. Querying just one required group
        # gives a superset; matches() still applies ALL groups and exclusions.
        # Short queries avoid sending long Boolean expressions to the API.
        keyword_groups = [min(keyword_groups, key=lambda group: sum(len(term) for term in group))]
    groups = ["(" + " OR ".join('(ti:"{0}" OR abs:"{0}")'.format(term.strip()) for term in group) + ")"
              for group in keyword_groups]
    # arXiv supports submittedDate filtering, but no lastUpdatedDate filter.
    # Sort updates descending and stop locally at the checkpoint instead.
    return " AND ".join(groups)


class RetryLaterError(RuntimeError):
    def __init__(self, message, retry_not_before):
        super().__init__(message)
        self.retry_not_before = retry_not_before


def retry_deadline(value, now):
    """Honor either form of Retry-After; use one minute when absent/invalid."""
    fallback = now + timedelta(seconds=60)
    try:
        if value and value.strip().isdigit():
            return max(fallback, now + timedelta(seconds=int(value.strip())))
        parsed = parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return max(fallback, parsed)
    except (TypeError, ValueError, OverflowError):
        return fallback


class ArxivClient:
    source = "api"

    def __init__(self, opener=urllib.request.urlopen, sleep=time.sleep, clock=time.monotonic):
        self.opener = opener
        self.sleep = sleep
        self.clock = clock
        self.last_request = None
        self.response_cache = {}

    def request(self, url):
        if url in self.response_cache:
            return self.response_cache[url]
        for attempt in range(3):
            if self.last_request is not None:
                self.sleep(max(0, REQUEST_INTERVAL - (self.clock() - self.last_request)))
            try:
                request = urllib.request.Request(url, headers={
                    "User-Agent": ARXIV_USER_AGENT,
                    "Accept": "application/atom+xml, application/xml;q=0.9, */*;q=0.1",
                })
                try:
                    with self.opener(request, timeout=45) as response:
                        payload = response.read()
                finally:
                    # Pace from response completion, including errors.
                    self.last_request = self.clock()
                self.response_cache[url] = payload
                return payload
            except urllib.error.HTTPError as error:
                retry_after = error.headers.get("Retry-After") if error.headers else None
                try:
                    detail = " ".join(error.read(500).decode("utf-8", errors="replace").split())[:240]
                except OSError:
                    detail = ""
                finally:
                    error.close()
                    self.last_request = self.clock()
                message = "arXiv HTTP {}: {}".format(error.code, detail or error.reason)
                if error.code == 429 or (error.code == 503 and retry_after):
                    deadline = stamp(retry_deadline(retry_after, datetime.now(timezone.utc)))
                    raise RetryLaterError(message + "; retry after " + deadline, deadline) from error
                if error.code not in (408, 500, 502, 503, 504) or attempt == 2:
                    raise RuntimeError(message) from error
                LOG.warning("%s，将在 %.0f 秒后重试", message, REQUEST_INTERVAL * 2 ** attempt)
                self.sleep(REQUEST_INTERVAL * 2 ** attempt)
            except Exception as error:
                if attempt == 2:
                    raise
                LOG.warning("请求失败，将重试 (%d/3): %s", attempt + 1, error)
                self.sleep(REQUEST_INTERVAL * 2 ** attempt)

    def fetch(self, topic, start, end):
        offset, expected = 0, None
        previous_updated = None
        seen = set()
        while True:
            params = {"search_query": make_query(topic, candidate_only=True), "start": offset,
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


class OaiClient(ArxivClient):
    """Harvest official metadata once, sharing matching candidates across topics."""

    source = "oai"

    def __init__(self, topics, **kwargs):
        super().__init__(**kwargs)
        self.topics = topics
        self.metadata_since = None
        self.window = None
        self.candidates = []
        self.stats = {"pages": 0, "records": 0, "duplicates": 0}

    def fetch(self, topic, start, end):
        window = (stamp(start), stamp(end))
        if self.window != window:
            # Keep only matching papers in memory. Unrelated metadata is not stored.
            self.stats = {"pages": 0, "records": 0, "duplicates": 0}
            candidates = {}
            for incoming in self.harvest(start, end):
                old = candidates.get(incoming["id"])
                if old is None or (date(incoming["updated"]), date(incoming["metadata_updated"])) >= (
                        date(old["updated"]), date(old["metadata_updated"])):
                    candidates[incoming["id"]] = incoming
            self.candidates = list(candidates.values())
            self.window = window
        yield from self.candidates

    def harvest(self, start, end):
        params = {"verb": "ListRecords", "metadataPrefix": "arXivRaw",
                  "from": (self.metadata_since or start).date().isoformat(), "until": end.date().isoformat()}
        tokens, seen, signatures = set(), set(), set()
        for page in range(1, 121):
            url = "https://oaipmh.arxiv.org/oai?" + urllib.parse.urlencode(params)
            payload = self.request(url)
            self.response_cache.pop(url, None)
            root = ET.fromstring(payload)
            if root.tag != "{" + OAI["o"] + "}OAI-PMH":
                raise ValueError("arXiv OAI 返回了无效元数据")
            errors = root.findall("o:error", OAI)
            if errors:
                if len(errors) == 1 and errors[0].get("code") == "noRecordsMatch" and page == 1:
                    LOG.info("OAI：时间窗口内没有元数据更新")
                    return
                raise ValueError("arXiv OAI: " + "; ".join(
                    (error.get("code") or "unknown") + ": " + (error.text or "") for error in errors))
            records = root.find("o:ListRecords", OAI)
            if records is None:
                raise ValueError("arXiv OAI 未返回 ListRecords")
            entries = records.findall("o:record", OAI)
            if not entries:
                raise ValueError("arXiv OAI 返回空分页但未声明 noRecordsMatch")
            LOG.info("OAI：读取第 %d 页，%d 条元数据，全部主题共用", page, len(entries))
            self.stats["pages"] += 1
            self.stats["records"] += len(entries)
            new_records = 0
            for record in entries:
                signature = hashlib.sha256(ET.tostring(record)).digest()
                if signature in signatures:
                    self.stats["duplicates"] += 1
                    continue
                signatures.add(signature)
                new_records += 1
                header = record.find("o:header", OAI)
                if header is None:
                    raise ValueError("arXiv OAI 记录缺少 header")
                if header.get("status") == "deleted":
                    continue
                raw = record.find("o:metadata/r:arXivRaw", OAI)
                if raw is None:
                    raise ValueError("arXiv OAI 记录缺少 arXivRaw 元数据")

                def field(name):
                    value = raw.findtext("r:" + name, namespaces=OAI)
                    if not value or not value.strip():
                        raise ValueError("arXiv OAI 论文缺少字段: " + name)
                    return " ".join(value.split())

                paper_id = field("id")
                if not re.fullmatch(r"(?:\d{4}\.\d{4,5}|[a-zA-Z.-]+/\d{7})", paper_id):
                    raise ValueError("arXiv OAI 返回无效论文 ID")
                if header.findtext("o:identifier", namespaces=OAI) != "oai:arXiv.org:" + paper_id:
                    raise ValueError("arXiv OAI 记录标识与论文 ID 不一致")
                if paper_id in seen:
                    self.stats["duplicates"] += 1
                seen.add(paper_id)
                candidate = {"id": paper_id, "title": field("title"), "abstract": field("abstract")}
                if not any(matches(candidate, topic) for topic in self.topics):
                    continue
                versions = {}
                for version in raw.findall("r:version", OAI):
                    number = version.get("version", "")
                    if not re.fullmatch(r"v[1-9]\d*", number) or number in versions:
                        raise ValueError("arXiv OAI 返回无效版本号")
                    value = parsedate_to_datetime(version.findtext("r:date", namespaces=OAI))
                    if value.tzinfo is None:
                        raise ValueError("arXiv OAI 版本时间缺少时区")
                    versions[number] = value
                if "v1" not in versions:
                    raise ValueError("arXiv OAI 缺少首次提交时间")
                published = versions["v1"]
                updated = versions[max(versions, key=lambda number: int(number[1:]))]
                if updated < published:
                    raise ValueError("arXiv OAI 论文修订早于首次提交")
                # OAI datestamps describe metadata changes; paper dates come only
                # from the version history, never the feed/harvest timestamp.
                if updated > end:
                    continue
                metadata_updated = header.findtext("o:datestamp", namespaces=OAI)
                if not metadata_updated:
                    raise ValueError("arXiv OAI 记录缺少元数据更新时间")
                if re.fullmatch(r"\d{4}-\d{2}-\d{2}", metadata_updated):
                    metadata_updated += "T00:00:00Z"
                yield dict(candidate, authors=[field("authors")], published=stamp(published),
                           updated=stamp(updated), metadata_updated=stamp(date(metadata_updated)),
                           url="https://arxiv.org/abs/" + paper_id,
                           pdf_url="https://arxiv.org/pdf/" + paper_id)
            if not new_records:
                raise ValueError("arXiv OAI 整页重复，分页未向前推进")
            token = records.findtext("o:resumptionToken", namespaces=OAI)
            if not token or not token.strip():
                return
            if token in tokens:
                raise ValueError("arXiv OAI 分页标记异常，未保存不完整结果")
            tokens.add(token)
            # Tokens are opaque, including any percent signs in their contents.
            params = {"verb": "ListRecords", "resumptionToken": token}
        raise ValueError("arXiv OAI 超过本轮分页上限，未前移成功检查点")


def sync(config, previous, client, now):
    state = copy.deepcopy(previous)
    fingerprint = hashlib.sha256(json.dumps(config["topics"], sort_keys=True).encode()).hexdigest()
    changed = fingerprint != previous.get("config_fingerprint")
    start = now - timedelta(days=config["initial_days"])
    if previous.get("last_success"):
        catchup = date(previous["last_success"]) - timedelta(days=config["overlap_days"])
        start = min(start, catchup) if changed else catchup
        if not changed and getattr(client, "source", "api") == "oai" and previous.get("source") == "oai":
            # OAI indexes changes by metadata modification date. An inclusive
            # one-day overlap covers date granularity without reharvesting a week.
            client.metadata_since = date(previous["last_success"]) - timedelta(days=1)
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
                 error=None, error_detail=None, retry_not_before=None, config_fingerprint=fingerprint,
                 source=getattr(client, "source", "api"), retrieval_stats=getattr(client, "stats", None))
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
        client = None
        try:
            if state.get("retry_not_before") and now < date(state["retry_not_before"]):
                raise RetryLaterError("arXiv 要求等待至 " + state["retry_not_before"], state["retry_not_before"])
            client = OaiClient(config["topics"]) if config.get("source", "api") == "oai" else ArxivClient()
            state = sync(config, state, client, now)
            LOG.info("完成检索：新增 %d 篇，共 %d 篇", len(state["last_new_ids"]), len(state["papers"]))
        except Exception as error:
            LOG.error("检索失败，保留历史数据: %s", error)
            state = dict(state, last_attempt=stamp(now), error="本次检索失败，已保留上次结果；下次运行将自动补抓。",
                         error_detail="{}: {}".format(type(error).__name__, error),
                         retry_not_before=getattr(error, "retry_not_before", None),
                         retrieval_stats=getattr(client, "stats", None))
            failed = True
        write_json(args.data, state)
    build(config, state, args.output)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
