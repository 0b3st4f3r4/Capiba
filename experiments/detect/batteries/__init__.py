"""Detection experiment batteries (baterias D-*).

Runners moved out of the production package (``src/capiba/detection/``):
they are only used by ``scripts/detect_battery.py`` and the slow test
batteries. Importable as ``batteries.battery_*`` when
``experiments/detect`` is on ``sys.path`` (pytest ``pythonpath`` setting;
``scripts/detect_battery.py`` inserts it explicitly).
"""
