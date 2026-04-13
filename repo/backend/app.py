"""Application container — wires services and runs the PyQt UI."""
from __future__ import annotations
import logging
import sys
import time

from . import config, db
from .services import (AuthService, BomService, CatalogService,
                       CheckpointService, EmployerComplianceService,
                       EvidenceService, HousingService, NotificationService,
                       ReportingService, ResourceService, SearchService,
                       SensitiveWordService, SettingsService, StudentService,
                       UpdaterService, ViolationActionService)
from .services.notification import install_trigger_handlers


# Startup-time profile data exposed for diagnostics ("how close to <5s?").
STARTUP_PROFILE: dict[str, float] = {}


class Container:
    def __init__(self) -> None:
        t0 = time.perf_counter()
        logging.basicConfig(filename=str(config.log_path()), level=logging.INFO,
                            format="%(asctime)s %(levelname)s %(name)s %(message)s")
        db.get_connection()
        STARTUP_PROFILE["db_open_s"] = time.perf_counter() - t0

        # On first run, copy the bundled update public key from the install
        # directory into the per-user data dir so UpdaterService finds it at
        # the documented runtime location.
        try:
            self._provision_update_pubkey()
        except Exception:
            logging.warning("could not provision update public key", exc_info=True)
        # Warn loudly if the deployed signing public key is still the shipped
        # placeholder. Signature verification rejects placeholder keys at
        # apply-time, but operators should see the misconfiguration during
        # boot — not only when an update is attempted.
        try:
            pk_path = config.update_signing_key_path()
            if pk_path.is_file() and b"PLACEHOLDER" in pk_path.read_bytes():
                logging.warning(
                    "update signing public key at %s is the shipped "
                    "PLACEHOLDER; signed updates will be rejected until a "
                    "real production key is installed.", pk_path)
        except Exception:
            pass

        t1 = time.perf_counter()
        db.seed_if_empty()
        STARTUP_PROFILE["seed_s"] = time.perf_counter() - t1

        t2 = time.perf_counter()
        self.auth = AuthService()
        self.students = StudentService()
        self.housing = HousingService()
        self.resources = ResourceService()
        self.compliance = EmployerComplianceService()
        self.evidence = EvidenceService()
        self.sensitive = SensitiveWordService()
        self.violations = ViolationActionService()
        self.notifications = NotificationService()
        self.search = SearchService()
        self.reporting = ReportingService()
        self.settings = SettingsService()
        self.catalog = CatalogService()
        self.bom = BomService()
        self.checkpoints = CheckpointService()
        self.updater = UpdaterService()
        install_trigger_handlers(self.notifications)
        STARTUP_PROFILE["services_s"] = time.perf_counter() - t2
        STARTUP_PROFILE["total_s"] = time.perf_counter() - t0
        if STARTUP_PROFILE["total_s"] > config.TARGET_STARTUP_SECONDS:
            logging.warning("Container init exceeded target: %.2fs > %.1fs",
                            STARTUP_PROFILE["total_s"],
                            config.TARGET_STARTUP_SECONDS)


    def _provision_update_pubkey(self) -> None:
        """Copy a real signing public key into the data dir on first run.

        Refuses to copy the documented ``update_pubkey.pem.example`` file —
        only an operator-supplied real PEM is provisioned. If nothing is
        provisioned, the updater rejects every package with
        ``SIGNATURE_REQUIRED`` until an operator drops in a real key, which
        is the intended fail-closed behaviour.
        """
        from pathlib import Path
        target = config.update_signing_key_path()
        if target.is_file():
            return
        candidates = [
            Path(__file__).resolve().parent.parent / "installer" / "update_pubkey.pem",
            Path(sys.argv[0]).resolve().parent / "update_pubkey.pem",
            Path(sys.argv[0]).resolve().parent / "installer" / "update_pubkey.pem",
        ]
        for src in candidates:
            if not src.is_file():
                continue
            data = src.read_bytes()
            # Hard guard: never provision the documentation stub.
            if (b"PLACEHOLDER" in data or b"EXAMPLE" in data
                    or b"-----BEGIN PUBLIC KEY-----" not in data):
                logging.warning(
                    "skipping non-production candidate update key at %s "
                    "(looks like a placeholder/example, not a real PEM)",
                    src)
                continue
            target.write_bytes(data)
            logging.info("provisioned update public key from %s", src)
            return


def run_gui() -> int:
    container = Container()
    try:
        from frontend.main_window import launch
    except ImportError:
        # Allow running as `python -m backend.app` from repo/.
        from ..frontend.main_window import launch  # type: ignore
    return launch(container)


if __name__ == "__main__":
    sys.exit(run_gui())
