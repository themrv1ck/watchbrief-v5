# WatchBrief V5 Report Schema Notes

The canonical machine-readable schema is:

- `schemas/single_video_report.schema.json`
- `schemas/watch_order.schema.json`

V5 keeps old V4 HTML output structure but removes legacy field control from the rendering contract.

Legacy fields such as `core_thesis`, `main_content_intro`, `summary_sentence`, `gain_actions`, `worth_watching`, `direct_watch_advice`, `recommended_sections`, and `worth_watching_sections` must not enter the V5 renderer.

