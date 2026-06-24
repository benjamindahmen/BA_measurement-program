from __future__ import annotations

import argparse
import sys
from pathlib import Path

from measurement.cellulink_api import CellulinkApiClient
from measurement.cellulink_auth import CellulinkAuthenticator
from measurement.config import load_config
from measurement.database import MeasurementDatabase
from measurement.gnss_reference import ReferenceGnssReader
from measurement.scheduler import MeasurementScheduler


def main() -> int:
    parser = argparse.ArgumentParser(description="Mobile Cellulink/GNSS measurement program")
    parser.add_argument(
        "--config",
        default=str(Path(__file__).with_name("config.ini")),
        help="Path to config.ini",
    )
    args = parser.parse_args()

    config = load_config(args.config)
    database = MeasurementDatabase(config.measurement.database_path)
    run_id: int | None = None
    scheduler: MeasurementScheduler | None = None
    gnss_reader: ReferenceGnssReader | None = None
    return_code = 0

    try:
        authenticator = CellulinkAuthenticator(config.cellulink)
        print("Checking Cellulink reachability...")
        authenticator.reachability_check()
        print("Logging in to Cellulink...")
        access_token = authenticator.login()

        api_client = CellulinkApiClient(config.cellulink, access_token)
        gnss_reader = ReferenceGnssReader(
            config.reference_gnss.port,
            config.reference_gnss.baudrate,
            config.reference_gnss.read_timeout_s,
            on_error=lambda message, details=None: database.log_error(run_id, "reference_gnss", message, details),
        )
        gnss_reader.start()

        run_id = database.create_run(
            config.measurement.route_id,
            config.measurement.direction,
            config.measurement.vehicle,
            config.measurement.notes,
            config.redacted_json(),
        )
        print(f"Measurement run {run_id} started. Database: {config.measurement.database_path}")

        _save_startup_snapshots(database, run_id, api_client)

        scheduler = MeasurementScheduler(config, database, run_id, api_client, gnss_reader)
        scheduler.start()
        scheduler.wait_forever()
    except KeyboardInterrupt:
        print("\nStopping measurement...")
    except Exception as exc:
        print(f"Fatal error: {exc}", file=sys.stderr)
        if run_id is not None:
            database.log_error(run_id, "main", str(exc))
        return_code = 1
    finally:
        if scheduler is not None:
            scheduler.stop()
        if gnss_reader is not None:
            gnss_reader.stop()
            gnss_reader.join(timeout=3)
        if run_id is not None:
            database.finish_run(run_id)
            print(f"Measurement run {run_id} finished.")
        database.close()
    return return_code


def _save_startup_snapshots(database: MeasurementDatabase, run_id: int, api_client: CellulinkApiClient) -> None:
    snapshots = [
        (
            f"/api/v1/cellular/modems/{api_client.config.modem_id}/profiles/{api_client.config.profile_id}/status",
            "cellular_profile_status",
            api_client.get_profile_status,
        ),
        (
            f"/api/v1/cellular/modems/{api_client.config.modem_id}/profiles/{api_client.config.profile_id}/configuration",
            "modem_profile_configuration",
            api_client.get_profile_configuration,
        ),
        (
            f"/api/v1/cellular/modems/{api_client.config.modem_id}/configuration",
            "cellular_configuration",
            api_client.get_modem_configuration,
        ),
        (
            f"/api/v1/cellular/modems/{api_client.config.modem_id}/information",
            "cellular_information",
            api_client.get_modem_information,
        ),
    ]
    for endpoint, snapshot_type, func in snapshots:
        try:
            payload = func()
            database.save_startup_snapshot(run_id, endpoint, snapshot_type, True, payload=payload)
        except Exception as exc:
            database.save_startup_snapshot(run_id, endpoint, snapshot_type, False, error_text=str(exc))
            database.log_error(run_id, f"startup_snapshot:{snapshot_type}", str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
