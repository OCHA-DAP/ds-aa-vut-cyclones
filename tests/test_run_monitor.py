import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
_SPEC = importlib.util.spec_from_file_location(
    "run_monitor", ROOT / "databricks" / "run_monitor.py"
)
rm = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(rm)

FMS = "pa-aa-fji-storms/raw/fms/inbox/abc123_f.csv"
VMHS = "ds-aa-vut-cyclones/raw/vmhs/inbox/abc123_v.csv"


@pytest.mark.parametrize(
    "event, blob, source",
    [
        ("fms_forecast", FMS, "fms"),
        ("fms_forecast_test", FMS, "fms"),
        ("vmhs_forecast", VMHS, "vmhs"),
        ("vmhs_forecast_test", VMHS, "vmhs"),
    ],
)
def test_monitor_args(event, blob, source):
    assert rm.monitor_args(event, blob) == [
        "--source",
        source,
        "--csv-blob",
        blob,
    ]


@pytest.mark.parametrize(
    "event, blob",
    [
        ("vmhs_forecast", FMS),  # a VMHS event must use the VMHS inbox
        ("fms_forecast", VMHS),
        ("vmhs_forecast", VMHS.replace("abc", "../x")),
        ("vmhs_forecast", "ds-aa-vut-cyclones/raw/vmhs/inbox/sub/x.csv"),
        ("vmhs_forecast", ""),
        ("other", VMHS),
    ],
)
def test_monitor_args_refuses(event, blob):
    with pytest.raises(ValueError):
        rm.monitor_args(event, blob)


def test_env_flag_is_strict():
    assert rm.env_flag("x", "True") is True
    assert rm.env_flag("x", "false") is False
    with pytest.raises(ValueError):
        rm.env_flag("x", "")


def test_prefixes_match_the_poller():
    # pa-aa-fji-storms pipelines/poll_fms_inbox.py stages here
    assert rm.EVENTS["fms_forecast"][1] == "pa-aa-fji-storms/raw/fms/inbox/"
    assert rm.EVENTS["vmhs_forecast"][1] == (
        "ds-aa-vut-cyclones/raw/vmhs/inbox/"
    )
