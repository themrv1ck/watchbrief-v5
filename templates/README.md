# WatchBefore Templates

最新单视频呈现规范见 [WATCHBEFORE_TEMPLATE_RULES.md](WATCHBEFORE_TEMPLATE_RULES.md)（2026-11-02 用户确认）。确认稿为 [watchbefore-summary-approved.html](watchbefore-summary-approved.html)。后续模板变更先对照该规范。

同一套多集内容的总入口使用[合集连续阅读规则](WATCHBEFORE_COLLECTION_RULES.md)，由 `scripts/collection_renderer.py` 与 `watchbefore-collection.css` 独立渲染：整套总结、主题阶段、连续分集内容和统一分集评分索引。当前样例待用户审阅；普通收藏列表仍使用观看顺序页。

普通报告与合集自 2026-11-03 起共享 `watchbefore-reading-theme.css`：石墨灰底、灰色正文，纯白仅用于标题。原确认稿只读保留，由生产 renderer 的后置主题覆盖旧亮色；合集的大方向与单集分别通过容器底板和编号缩进突出。

字段占位模板源继续保存在 `../references/`：

- `../references/video_report_v5.html`
- `../references/00watch_order_v5.html`

生产 renderer 已于 2026-11-02 接入该规范，并以 7 条真实列表报告完成结构、来源覆盖率、顺序和入口文件核验。[验收记录](../outputs/watchbefore-playlist-validation-20261102.json)与浏览器视觉验收分开记录。
