"""The Vanuatu Cyclone Monitoring job's one task: the trigger monitor.

Runs ``pipelines/monitor_forecast_trigger.py`` (trigger check and the
Listmonk emails) on the CSV the FMS relay poller in pa-aa-fji-storms staged
in the dev blob:

- an RSMC Nadi (FMS) forecast (``fms_forecast``, staged by the poller for
  the Fiji job too) -> the readiness trigger;
- a VMHS forecast (``vmhs_forecast``, from the "[vut-relay]" relay emails)
  -> the action and observational triggers.

The FMS CSV is read straight from the staged file, not from storms.fms_*:
the Fiji job ingests it at the same moment, and this job must not wait on
or race that. Adapted from pa-aa-fji-storms (databricks/run_monitor.py).

Positional parameters (databricks.yml):
    1 event                   fms_forecast | fms_forecast_test |
                              vmhs_forecast | vmhs_forecast_test (a test
                              event forces TEST_EMAIL=True)
    2 test_email              True | False (from the poller)
    3 csv_blob                the staged CSV
    4 dry_run                 True | False (fixed at deploy)
    5 simulate_trigger        True | False (fixed at deploy)
    6 max_forecast_age_hours  no real-list emails for older forecasts

src/ and pipelines/ are copied off the workspace mount to local disk first
(imports from the wsfs clone are intermittently flaky). Never ``sys.exit``
here: the DBX exec context treats even ``SystemExit(0)`` as a failure.
"""

import os
import shutil
import subprocess
import sys
import tempfile

# event -> (source, staged-CSV prefix); the prefixes must match
# pa-aa-fji-storms pipelines/poll_fms_inbox.py (INBOX_PREFIX,
# VUT_INBOX_PREFIX)
EVENTS = {
    "fms_forecast": ("fms", "pa-aa-fji-storms/raw/fms/inbox/"),
    "fms_forecast_test": ("fms", "pa-aa-fji-storms/raw/fms/inbox/"),
    "vmhs_forecast": ("vmhs", "ds-aa-vut-cyclones/raw/vmhs/inbox/"),
    "vmhs_forecast_test": ("vmhs", "ds-aa-vut-cyclones/raw/vmhs/inbox/"),
}
TEST_EVENTS = ("fms_forecast_test", "vmhs_forecast_test")
LISTMONK_SECRETS = (
    "DSCI_LISTMONK_BASE_URL",
    "DSCI_LISTMONK_API_USERNAME",
    "DSCI_LISTMONK_API_KEY",
)


def _find_script_dir() -> str:
    """spark_python_task's exec context doesn't always define __file__."""
    try:
        return os.path.dirname(os.path.abspath(__file__))  # noqa: F821
    except NameError:
        pass
    if sys.argv and sys.argv[0]:
        return os.path.dirname(os.path.abspath(sys.argv[0]))
    return os.getcwd()


def _arg(i: int) -> str:
    return sys.argv[i].strip() if len(sys.argv) > i else ""


def env_flag(name: str, value: str) -> bool:
    """Strict: anything but true/false (any case) is an error."""
    if value.lower() in ("true", "1", "yes"):
        return True
    if value.lower() in ("false", "0", "no"):
        return False
    raise ValueError(f"{name} must be True or False, got {value!r}")


def monitor_args(event: str, csv_blob: str) -> list[str]:
    """The monitor's arguments; refuses a CSV outside the event's inbox."""
    if event not in EVENTS:
        raise ValueError(f"event must be one of {list(EVENTS)}, got {event!r}")
    source, prefix = EVENTS[event]
    if (
        not csv_blob.startswith(prefix)
        or ".." in csv_blob
        or "/" in csv_blob.removeprefix(prefix)
    ):
        raise ValueError(f"csv_blob must be a file in {prefix}: {csv_blob!r}")
    return ["--source", source, "--csv-blob", csv_blob]


def main() -> None:
    from databricks.sdk.runtime import dbutils

    event = _arg(1)
    test_email = env_flag("test_email", _arg(2))
    csv_blob = _arg(3)
    dry_run = env_flag("dry_run", _arg(4))
    simulate = env_flag("simulate_trigger", _arg(5))
    max_age_hours = _arg(6)
    if event in TEST_EVENTS:
        test_email = True
    input_args = monitor_args(event, csv_blob)

    for key in LISTMONK_SECRETS:
        try:
            os.environ[key] = dbutils.secrets.get("dsci", key)
        except Exception as exc:  # noqa: BLE001
            if not dry_run:
                raise RuntimeError(f"dsci/{key} unavailable: {exc}") from None
            print(f"[run_monitor] WARNING: dsci/{key} unavailable ({exc})")

    repo_root = os.path.abspath(os.path.join(_find_script_dir(), ".."))
    local_root = os.path.join(
        "/local_disk0"
        if os.path.isdir("/local_disk0")
        else tempfile.gettempdir(),
        "vut_monitor",
    )
    shutil.rmtree(local_root, ignore_errors=True)
    for sub in ("src", "pipelines"):
        shutil.copytree(
            os.path.join(repo_root, sub),
            os.path.join(local_root, sub),
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )

    env = dict(os.environ)
    env.update(
        {
            "TEST_EMAIL": str(test_email),
            "DRY_RUN": str(dry_run),
            "SIMULATE_TRIGGER": str(simulate),
            "MAX_FORECAST_AGE_HOURS": max_age_hours,
            "PYTHONPATH": local_root + os.pathsep + env.get("PYTHONPATH", ""),
            "PYTHONUNBUFFERED": "1",
            "MPLCONFIGDIR": "/tmp/mplconfig",
        }
    )
    cmd = [
        sys.executable,
        os.path.join(local_root, "pipelines", "monitor_forecast_trigger.py"),
        *input_args,
    ]
    print(
        f"[run_monitor] event={event} TEST_EMAIL={test_email} "
        f"DRY_RUN={dry_run} SIMULATE_TRIGGER={simulate} "
        f"MAX_FORECAST_AGE_HOURS={max_age_hours} args={' '.join(input_args)}",
        flush=True,
    )
    rc = subprocess.run(cmd, cwd=local_root, env=env, check=False).returncode
    if rc != 0:
        raise RuntimeError(
            f"monitor_forecast_trigger.py exited with code {rc}"
        )
    print("[run_monitor] OK")


if __name__ == "__main__":
    main()
