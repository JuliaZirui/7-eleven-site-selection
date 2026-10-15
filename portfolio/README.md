# Portfolio

| File | Contents |
| --- | --- |
| [portfolio.pdf](portfolio.pdf) | The two-page A4 portfolio; `portfolio_p1.png` and `portfolio_p2.png` are its pages as images |
| [architecture_v2.svg](architecture_v2.svg) | System architecture diagram used in the portfolio |
| [results.pdf](results.pdf) | Case-study charts on their own (A4 landscape, two pages) |
| [project_brief.pdf](project_brief.pdf) | Earlier two-page project brief |
| `architecture*.svg/pdf/png` | Earlier versions of the architecture diagram (simplified and detailed) |

Every figure comes from analysis run #26 and is recomputed with the project's own scoring code, so the charts match the published report.

## Rebuilding

```bash
python3 portfolio/extract_results.py db/site_selection.db   # chart data -> results_data.json (read-only on the DB)
python3 portfolio/make_architecture_v2.py                    # architecture_v2.svg
python3 portfolio/make_results.py                            # results.html
python3 portfolio/make_portfolio.py                          # portfolio.html
```

Then print each `.html` page to PDF with a Chromium-based browser (no margins, background graphics on), for example:

```bash
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new --no-pdf-header-footer --print-to-pdf=portfolio/portfolio.pdf "file://$PWD/portfolio/portfolio.html"
```

`make_brief_sec4.py` rewrites section 4 of `project_brief.html`; `make_architecture.py` and `make_architecture_simple.py` build the earlier diagrams.
