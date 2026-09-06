"use strict";
const data = JSON.parse(document.getElementById("paper-data").textContent);
const $ = (id) => document.getElementById(id);
const topicNames = new Map(data.topics.map((topic) => [topic.id, topic.name]));
const activePapers = data.papers.filter((paper) => paper.topics.some((id) => topicNames.has(id)));
const recentIds = new Set(data.last_new_ids);
let limit = 40;
const formatTime = (value) => new Intl.DateTimeFormat("zh-CN", {timeZone: "Asia/Shanghai", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", year: "numeric", hour12: false}).format(new Date(value));
const day = (value) => new Intl.DateTimeFormat("sv-SE", {timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit"}).format(new Date(value));
function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}
function link(text, url, className) {
  const node = element("a", className, text);
  // Only arXiv links are rendered as external navigation.
  if (/^https:\/\/arxiv\.org\/(abs|pdf)\//.test(url)) node.href = url;
  node.target = "_blank";
  node.rel = "noopener noreferrer";
  return node;
}
$('total-count').textContent = activePapers.length;
$('topic-count').textContent = data.topics.length;
$('new-count').textContent = data.last_success ? activePapers.filter((p) => recentIds.has(p.id)).length : "—";
if (data.last_success) $('updated').textContent = "最近成功更新 " + formatTime(data.last_success);
if (data.error) {
  $('status').textContent = data.error + (data.last_attempt ? " 最近尝试：" + formatTime(data.last_attempt) : "");
  $('status').classList.add('warning');
} else if (!data.last_success) {
  $('status').textContent = "等待首次检索。完成后会显示最近 30 天的相关论文。";
} else if (Date.now() - Date.parse(data.last_success) > 48 * 60 * 60 * 1000) {
  $('status').textContent = "超过 48 小时未成功更新，请检查定时任务。当前显示已保存的论文。";
  $('status').classList.add('warning');
} else {
  $('status').textContent = data.last_new_ids.length ? "新增标签表示最近一次成功检索首次收录的论文。" : "最近一次检索没有发现新论文，历史论文仍可浏览。";
}
for (const topic of data.topics) {
  const option = element('option', '', topic.name);
  option.value = topic.id;
  $('topic').append(option);
}
function render() {
  const query = $('search').value.trim().toLocaleLowerCase();
  const topic = $('topic').value;
  const period = $('period').value;
  const custom = period === 'custom';
  $('from-label').hidden = $('to-label').hidden = !custom;
  const earliest = ['7','30'].includes(period) ? day(Date.now() - (Number(period) - 1) * 86400000) : '';
  const filtered = activePapers.filter((paper) => {
    const published = day(paper.published);
    return (!topic || paper.topics.includes(topic)) &&
      (!query || [paper.title, paper.abstract, ...paper.authors].join(' ').toLocaleLowerCase().includes(query)) &&
      (!earliest || published >= earliest) &&
      (!custom || ((!$('from').value || published >= $('from').value) && (!$('to').value || published <= $('to').value)));
  });
  $('result-count').textContent = filtered.length + " 篇";
  $('papers').replaceChildren();
  const fragment = document.createDocumentFragment();
  for (const paper of filtered.slice(0, limit)) {
    const article = element('article', 'paper');
    const meta = element('div', 'paper-date');
    const time = element('time', '', day(paper.published));
    time.dateTime = paper.published;
    meta.append(time);
    if (recentIds.has(paper.id)) meta.append(element('span', 'badge', '新增'));
    const body = element('div', 'paper-body');
    const title = element('h3');
    title.append(link(paper.title, paper.url));
    const tags = element('div', 'tags');
    paper.topics.filter((id) => topicNames.has(id)).forEach((id) => tags.append(element('span', 'tag', topicNames.get(id))));
    const actions = element('div', 'paper-actions');
    const details = element('details');
    const summary = element('summary', '', '展开原文摘要');
    details.addEventListener('toggle', () => { summary.textContent = details.open ? '收起原文摘要' : '展开原文摘要'; });
    details.append(summary, element('p', '', paper.abstract));
    actions.append(details, link('PDF ↗', paper.pdf_url, 'pdf'));
    body.append(title, element('p', 'authors', paper.authors.join(', ')), tags, actions);
    article.append(meta, body);
    fragment.append(article);
  }
  $('papers').append(fragment);
  $('empty').hidden = filtered.length !== 0;
  if (!filtered.length) {
    $('empty').querySelector('h3').textContent = activePapers.length ? '没有匹配的论文' : '暂无论文';
    $('empty').querySelector('p').textContent = activePapers.length ? '试试其他关键词或扩大日期范围。' : data.last_success ? '本次检索没有找到符合订阅条件的论文。' : '首次检索完成后，相关论文会出现在这里。';
  }
  $('more').hidden = filtered.length <= limit;
}
['search','topic','period','from','to'].forEach((id) => $(id).addEventListener('input', () => {limit = 40; render();}));
$('more').addEventListener('click', () => {limit += 40; render();});
render();
