import io

import pytest

pytest.importorskip("pandas")
import sys  # noqa: E402
from pathlib import Path  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "layers" / "l6_radiation_atmo"))
import fetch as F  # noqa: E402

RAD_SAMPLE = """[FILE]
START_SOL=0453

[OBSERVATION: 00]
START_OBS_MARS="0453 11:00:44"
START_OBS_UTC="2013-318 12:01:58"
ROVER_LAT=0.000
QC=00000000

[DOSIMETRY: 00]

[DOSIMETRY_TOTAL_DOSE_B: 00]

8.82310

[DOSIMETRY_TOTAL_DOSE_E: 00]

8.54490

[OBSERVATION: 01]
START_OBS_MARS=N/A

[OBSERVATION: 02]
START_OBS_MARS="0453 11:35:00"

[DOSIMETRY_TOTAL_DOSE_B: 02]

9.0
"""


def test_rad_parser_reads_dose_per_observation():
    rows = list(F.parse_rad_stream(io.StringIO(RAD_SAMPLE), "RAD_RDR_2013_318_12_01_0453_V00.TXT"))
    assert [r["obs"] for r in rows] == [0, 1, 2]
    assert rows[0]["dose_rate_b_ugy_h"] == pytest.approx(8.8231)
    assert rows[0]["dose_rate_e_ugy_h"] == pytest.approx(8.5449)
    assert "dose_rate_b_ugy_h" not in rows[1]
    assert rows[1]["sol"] == 453
    assert rows[2]["dose_rate_b_ugy_h"] == pytest.approx(9.0)
