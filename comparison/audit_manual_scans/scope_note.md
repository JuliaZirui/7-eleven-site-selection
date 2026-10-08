# Note on the competitor scope (analyst, 2026-10-08)

In the manual scans of the generalisation stations the analyst marked wine shops (e.g. *Jacques' Wein-Depot*, *Württemberger Weinhaus*, *Der Reblauser Weinladen*, *Garlipp Weinhandlung*, *Wine Damager*) and the drugstore *dm* as `category_error`. Under the project's competitor taxonomy (`src/poi_taxonomy.py`, v2) beverage stores (Getränkemarkt, wine and spirits shops) and drugstores (Drogerie: dm, Rossmann) are in scope on purpose, as competitors for the drinks and toiletries share of a convenience basket.

Asked whether these marks mean data errors or disagreement with that definition, the analyst said the **scope definition itself is the problem**.

Consequence for the audit evaluation: these findings are reported as a challenge to the competitor scope, not as data errors the auditor missed. The auditor works to the current definition, so they do not count against its recall on the generalisation stations. Whether the scope should change is a separate decision for the scoring model, outside this experiment.
