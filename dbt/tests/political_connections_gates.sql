-- P8 (PR-D-08): every publishable political_connection row honors the
-- pre-registered gates — donation >= 1000 (floor), share >= 0.05
-- (concentration), score = in [0, 1] and the signature dates inside the
-- mandate window are guaranteed upstream by the signal (details carry the
-- window). Thresholds mirror experiments/detect/D-08.json; changing them
-- requires a PR-D-08b. NULL comparisons evaluate to NULL and are not
-- returned; any returned row is a gate violation.
select *
from {{ ref('political_connections') }}
where donation_total_brl < 1000
   or share < 0.05
   or score < 0
   or score > 1
