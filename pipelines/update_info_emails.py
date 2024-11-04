import argparse
import os

from dotenv import load_dotenv

from src.constants import INPUT_DIR, OUTPUT_DIR
from src.datasources.fms import (
    calculate_distances,
    decode_forecast_csv,
    get_report,
    process_fms_forecast,
)
from src.email.plotting import plot_distances, plot_forecast
from src.email.send_emails import send_info_email
from src.utils.simex_utils import load_simex_inject

load_dotenv()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    test_csv = os.getenv("TEST_CSV")
    parser.add_argument("csv", nargs="?", type=str, default=test_csv)
    parser.add_argument("--suppress-send", action="store_true")
    parser.add_argument("--test-email", action="store_true")
    parser.add_argument("--csv-env-var-name")
    parser.add_argument("--simex-inject")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    if not OUTPUT_DIR.exists():
        os.mkdir(OUTPUT_DIR)
    if not INPUT_DIR.exists():
        os.mkdir(INPUT_DIR)
    if args.csv_env_var_name is None:
        if args.simex_inject is None:
            csv_str = args.csv
        else:
            csv_str = load_simex_inject(args.simex_inject)
    else:
        csv_str = os.getenv(args.csv_env_var_name)
    filepath = decode_forecast_csv(csv_str)
    forecast = process_fms_forecast(
        path=filepath, save_local=True, save_blob=False
    )
    report = get_report(forecast)
    plot_forecast(report, forecast, save_html=True)
    distances = calculate_distances(report, forecast)
    min_distance = distances["distance (km)"].min()
    print(f"Minimum distance: {min_distance} km")
    plot_distances(report, distances)
    send_info_email(
        report,
        min_distance=min_distance,
        suppress_send=args.suppress_send,
        test_email=args.test_email,
    )
