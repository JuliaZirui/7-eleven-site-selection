# VBB GTFS snapshot

| File | In repo | Used by |
| --- | --- | --- |
| `vbb_gtfs_20260924_stops.zip` | yes (1.2 MB) | `attraction_pipeline.rail_stations()`: station lookups in the metrics pipeline, demand calibration and the test suite |
| `vbb_gtfs_20260924.zip` | no (~75 MB) | `transit_supply.py` only (departures per hour from `stop_times.txt`); its output is committed as `reports/transit_supply_20260928T143558Z.json` |

The extract holds `stops.txt` unchanged from the full feed (VBB, fetched 2026-09-24, feed valid 2026-09-24 to 2026-12-12, CC BY 4.0).

To rerun `transit_supply.py`, download the current feed from the [VBB open data page](https://unternehmen.vbb.de/digitale-services/datensaetze/) and save it as `vbb_gtfs_20260924.zip` here. A newer feed will give slightly different departure counts than the committed output.
