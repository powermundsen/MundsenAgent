"""Private SQLite storage and Apple Health XML import for training context."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
import tempfile
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO
from typing import Iterator
from zoneinfo import ZoneInfo

from mundsen_agent.instance import InstanceSettings
from mundsen_agent.module_system import ModuleHealth, ModuleNotification
from mundsen_agent.paths import MundsenAgentPaths, ensure_private_directories

MAX_HEALTH_XML_BYTES = 8 * 1024 * 1024 * 1024
SUMMARY_DAYS = 90
_RECORD_TYPES = frozenset(
    {
        "HKQuantityTypeIdentifierActiveEnergyBurned",
        "HKQuantityTypeIdentifierAppleExerciseTime",
        "HKQuantityTypeIdentifierBodyMass",
        "HKQuantityTypeIdentifierDistanceCycling",
        "HKQuantityTypeIdentifierDistanceWalkingRunning",
        "HKQuantityTypeIdentifierHeartRate",
        "HKQuantityTypeIdentifierHeartRateVariabilitySDNN",
        "HKQuantityTypeIdentifierRestingHeartRate",
        "HKQuantityTypeIdentifierStepCount",
        "HKQuantityTypeIdentifierVO2Max",
    }
)


class LocalHealthError(RuntimeError):
    """Raised when local health data is malformed or stored unsafely."""


@dataclass(frozen=True, slots=True)
class ImportResult:
    import_id: str
    workouts_added: int
    samples_added: int
    already_imported: bool


def _parse_time(value: str) -> int:
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d %H:%M:%S %z")
    except ValueError as exc:
        raise LocalHealthError("Apple Health contains an invalid timestamp") from exc
    return int(parsed.astimezone(timezone.utc).timestamp())


def _number(value: str, *, field: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise LocalHealthError(f"Apple Health contains an invalid {field}") from exc
    if parsed != parsed or parsed in {float("inf"), float("-inf")}:
        raise LocalHealthError(f"Apple Health contains an invalid {field}")
    return parsed


def _duration_seconds(value: str, unit: str) -> float:
    duration = _number(value, field="duration")
    factors = {"s": 1.0, "sec": 1.0, "min": 60.0, "hr": 3600.0}
    if unit not in factors or duration < 0:
        raise LocalHealthError("Apple Health contains an unsupported duration")
    return duration * factors[unit]


def _distance_km(value: str, unit: str) -> float:
    distance = _number(value, field="distance")
    factors = {"km": 1.0, "m": 0.001, "mi": 1.609344}
    return distance * factors.get(unit, 0.0) if distance >= 0 else 0.0


def _energy_kcal(value: str, unit: str) -> float:
    energy = _number(value, field="energy")
    if energy < 0:
        return 0.0
    if unit in {"kcal", "Cal"}:
        return energy
    if unit == "kJ":
        return energy / 4.184
    return 0.0


def _event_id(kind: str, values: tuple[object, ...]) -> str:
    canonical = "\x1f".join([kind, *(str(value) for value in values)])
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class LocalHealthModule:
    name = "local-health"

    def __init__(self, paths: MundsenAgentPaths, instance: InstanceSettings) -> None:
        self.paths = paths
        self.instance = instance
        self.root = paths.state_dir / "modules" / self.name
        self.database = self.root / "health.sqlite3"
        self.summary = self.root / "training-summary.md"
        self.summary_json = self.root / "training-summary.json"

    def _connect(self) -> sqlite3.Connection:
        if self.database.is_symlink():
            raise LocalHealthError("local health database is unsafe")
        ensure_private_directories((self.root,))
        connection = sqlite3.connect(self.database)
        os.chmod(self.database, 0o600)
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS imports (
                import_id TEXT PRIMARY KEY,
                imported_at INTEGER NOT NULL,
                workouts_added INTEGER NOT NULL,
                samples_added INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS workouts (
                id TEXT PRIMARY KEY,
                activity_type TEXT NOT NULL,
                start_epoch INTEGER NOT NULL,
                end_epoch INTEGER NOT NULL,
                duration_seconds REAL NOT NULL,
                distance_km REAL NOT NULL,
                energy_kcal REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS workouts_start ON workouts(start_epoch);
            CREATE TABLE IF NOT EXISTS samples (
                id TEXT PRIMARY KEY,
                sample_type TEXT NOT NULL,
                start_epoch INTEGER NOT NULL,
                end_epoch INTEGER NOT NULL,
                value REAL NOT NULL,
                unit TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS samples_type_start
                ON samples(sample_type, start_epoch);
            """
        )
        return connection

    @contextmanager
    def _database_connection(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def import_apple_health(self, source: str | Path) -> ImportResult:
        path = Path(source)
        if not path.is_absolute() or path.is_symlink():
            raise LocalHealthError("Apple Health export path is unsafe")
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(path, flags)
        except OSError as exc:
            raise LocalHealthError("Apple Health export path is unsafe") from exc
        try:
            file_stat = os.fstat(descriptor)
            if (
                not stat.S_ISREG(file_stat.st_mode)
                or file_stat.st_size > MAX_HEALTH_XML_BYTES
            ):
                raise LocalHealthError("Apple Health export is too large or unsupported")
            handle = os.fdopen(descriptor, "rb")
            descriptor = -1
            digest = hashlib.sha256()
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
            import_id = digest.hexdigest()
            with self._database_connection() as database:
                if database.execute(
                    "SELECT 1 FROM imports WHERE import_id = ?", (import_id,)
                ).fetchone():
                    return ImportResult(import_id, 0, 0, True)
                handle.seek(0)
                workouts, samples = self._import_stream(database, handle)
                database.execute(
                    "INSERT INTO imports VALUES (?, ?, ?, ?)",
                    (
                        import_id,
                        int(datetime.now(timezone.utc).timestamp()),
                        workouts,
                        samples,
                    ),
                )
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            elif "handle" in locals():
                handle.close()
        self.write_summary()
        return ImportResult(import_id, workouts, samples, False)

    def _import_stream(
        self,
        database: sqlite3.Connection,
        source: BinaryIO,
    ) -> tuple[int, int]:
        workouts = 0
        samples = 0
        try:
            iterator = ET.iterparse(source, events=("start", "end"))
            root_seen = False
            for event, element in iterator:
                if not root_seen:
                    if event != "start" or element.tag != "HealthData":
                        raise LocalHealthError("Apple Health export has an invalid root")
                    root_seen = True
                    continue
                if event != "end":
                    continue
                attributes = element.attrib
                if element.tag == "Workout":
                    start = _parse_time(attributes.get("startDate", ""))
                    end = _parse_time(attributes.get("endDate", ""))
                    activity = attributes.get("workoutActivityType", "")[:160]
                    if end < start or not activity:
                        raise LocalHealthError("Apple Health contains an invalid workout")
                    duration = _duration_seconds(
                        attributes.get("duration", "0"),
                        attributes.get("durationUnit", "min"),
                    )
                    distance = _distance_km(
                        attributes.get("totalDistance", "0"),
                        attributes.get("totalDistanceUnit", "km"),
                    )
                    energy = _energy_kcal(
                        attributes.get("totalEnergyBurned", "0"),
                        attributes.get("totalEnergyBurnedUnit", "kcal"),
                    )
                    values = (activity, start, end, duration, distance, energy)
                    before = database.total_changes
                    database.execute(
                        "INSERT OR IGNORE INTO workouts VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (_event_id("workout", values), *values),
                    )
                    workouts += database.total_changes - before
                elif element.tag == "Record":
                    sample_type = attributes.get("type", "")
                    if sample_type in _RECORD_TYPES:
                        start = _parse_time(attributes.get("startDate", ""))
                        end = _parse_time(attributes.get("endDate", ""))
                        if end < start:
                            raise LocalHealthError("Apple Health contains an invalid sample")
                        value = _number(attributes.get("value", ""), field="value")
                        unit = attributes.get("unit", "")[:40]
                        values = (sample_type, start, end, value, unit)
                        before = database.total_changes
                        database.execute(
                            "INSERT OR IGNORE INTO samples VALUES (?, ?, ?, ?, ?, ?)",
                            (_event_id("sample", values), *values),
                        )
                        samples += database.total_changes - before
                element.clear()
        except ET.ParseError as exc:
            raise LocalHealthError("Apple Health export is not valid XML") from exc
        return workouts, samples

    def write_summary(self, *, now: datetime | None = None) -> Path:
        clock = now or datetime.now(timezone.utc)
        cutoff = int(clock.timestamp()) - SUMMARY_DAYS * 86400
        recent_cutoff = int(clock.timestamp()) - 14 * 86400
        ensure_private_directories((self.root,))
        with self._database_connection() as database:
            recent = database.execute(
                "SELECT COUNT(*), COALESCE(SUM(duration_seconds), 0) "
                "FROM workouts WHERE start_epoch >= ?",
                (recent_cutoff,),
            ).fetchone()
            groups = database.execute(
                "SELECT activity_type, COUNT(*), SUM(duration_seconds), "
                "SUM(distance_km), SUM(energy_kcal) FROM workouts "
                "WHERE start_epoch >= ? GROUP BY activity_type "
                "ORDER BY SUM(duration_seconds) DESC",
                (cutoff,),
            ).fetchall()
            latest_workouts = database.execute(
                "SELECT activity_type, start_epoch, duration_seconds, distance_km "
                "FROM workouts ORDER BY start_epoch DESC LIMIT 10"
            ).fetchall()
            latest_metrics = database.execute(
                "SELECT s.sample_type, s.value, s.unit, s.start_epoch "
                "FROM samples s JOIN ("
                "SELECT sample_type, MAX(start_epoch) AS latest FROM samples "
                "GROUP BY sample_type) latest "
                "ON s.sample_type = latest.sample_type AND s.start_epoch = latest.latest "
                "ORDER BY s.sample_type"
            ).fetchall()
        generated_at = clock.astimezone(timezone.utc).isoformat()
        lines = [
            "# Local training summary",
            "",
            f"Generated: {generated_at}",
            f"Window: last {SUMMARY_DAYS} days",
            "",
            "## Last 14 days",
            "",
            f"Workouts: {int(recent[0])}",
            f"Duration: {float(recent[1]) / 3600:.1f} hours",
            "",
            "## Activity totals",
            "",
        ]
        if not groups:
            lines.append("No workouts imported.")
        for activity, count, duration, distance, energy in groups:
            label = str(activity).removeprefix("HKWorkoutActivityType") or "Other"
            lines.append(
                f"- {label}: {count} workouts, {float(duration) / 3600:.1f} h, "
                f"{float(distance):.1f} km, {float(energy):.0f} kcal"
            )
        lines.extend(["", "## Latest workouts", ""])
        if not latest_workouts:
            lines.append("No workouts imported.")
        zone = ZoneInfo(self.instance.timezone)
        for activity, start, duration, distance in latest_workouts:
            day = datetime.fromtimestamp(int(start), timezone.utc).astimezone(zone).date()
            label = str(activity).removeprefix("HKWorkoutActivityType") or "Other"
            lines.append(
                f"- {day.isoformat()} {label}: {float(duration) / 60:.0f} min, "
                f"{float(distance):.1f} km"
            )
        lines.extend(["", "## Latest selected metrics", ""])
        if not latest_metrics:
            lines.append("No selected metrics imported.")
        for sample_type, value, unit, start in latest_metrics:
            label = str(sample_type).removeprefix("HKQuantityTypeIdentifier")
            day = datetime.fromtimestamp(int(start), timezone.utc).astimezone(zone).date()
            lines.append(f"- {label}: {float(value):.2f} {unit} ({day.isoformat()})")
        lines.extend(
            [
                "",
                "This summary supports training reflection, not medical diagnosis.",
                "",
            ]
        )
        payload = {
            "schema_version": 1,
            "generated_at": generated_at,
            "window_days": SUMMARY_DAYS,
            "last_14_days": {
                "workouts": int(recent[0]),
                "duration_hours": round(float(recent[1]) / 3600, 2),
            },
            "activity_totals": [
                {
                    "activity_type": str(activity).removeprefix(
                        "HKWorkoutActivityType"
                    )
                    or "Other",
                    "workouts": int(count),
                    "duration_hours": round(float(duration) / 3600, 2),
                    "distance_km": round(float(distance), 3),
                    "energy_kcal": round(float(energy), 2),
                }
                for activity, count, duration, distance, energy in groups
            ],
            "latest_workouts": [
                {
                    "activity_type": str(activity).removeprefix(
                        "HKWorkoutActivityType"
                    )
                    or "Other",
                    "date": datetime.fromtimestamp(
                        int(start), timezone.utc
                    ).astimezone(zone).date().isoformat(),
                    "duration_minutes": round(float(duration) / 60, 2),
                    "distance_km": round(float(distance), 3),
                }
                for activity, start, duration, distance in latest_workouts
            ],
            "latest_metrics": [
                {
                    "sample_type": str(sample_type).removeprefix(
                        "HKQuantityTypeIdentifier"
                    ),
                    "value": float(value),
                    "unit": str(unit),
                    "date": datetime.fromtimestamp(
                        int(start), timezone.utc
                    ).astimezone(zone).date().isoformat(),
                }
                for sample_type, value, unit, start in latest_metrics
            ],
            "medical_use": False,
        }
        self._atomic_private_write(self.summary, "\n".join(lines))
        self._atomic_private_write(
            self.summary_json,
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
        )
        return self.summary

    def _atomic_private_write(self, target: Path, text: str) -> None:
        descriptor, temporary = tempfile.mkstemp(prefix=".summary-", dir=self.root)
        temp_path = Path(temporary)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                descriptor = -1
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, target)
            os.chmod(target, 0o600)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            temp_path.unlink(missing_ok=True)

    def context(self) -> str:
        if not self.summary.exists():
            return "Local health is enabled, but no training summary has been generated."
        if self.summary.is_symlink() or stat.S_IMODE(self.summary.stat().st_mode) != 0o600:
            raise LocalHealthError("local training summary is unsafe")
        return self.summary.read_text(encoding="utf-8")[:32_000]

    def health(self) -> list[ModuleHealth]:
        if not self.database.exists():
            return [ModuleHealth("module.local-health", True, "enabled; no data imported")]
        private_files = (self.database, self.summary, self.summary_json)
        safe = all(
            path.is_file()
            and not path.is_symlink()
            and stat.S_IMODE(path.stat().st_mode) == 0o600
            for path in private_files
        )
        return [
            ModuleHealth(
                "module.local-health",
                safe,
                "private local database" if safe else "unsafe database permissions",
            )
        ]

    def run_scheduled(self, now: datetime) -> None:
        if not self.database.exists():
            return
        if self.database.is_symlink():
            raise LocalHealthError("local health database is unsafe")
        if self.summary.is_symlink() or self.summary_json.is_symlink():
            raise LocalHealthError("local training summary is unsafe")
        summary_missing = not self.summary.exists() or not self.summary_json.exists()
        summary_stale = (
            not summary_missing
            and self.database.stat().st_mtime
            > min(self.summary.stat().st_mtime, self.summary_json.stat().st_mtime)
        )
        if summary_missing or summary_stale:
            self.write_summary(now=now)

    def pending_notifications(self) -> list[ModuleNotification]:
        return []

    def mark_notification_sent(self, key: str) -> None:
        raise ValueError(f"local-health has no scheduled notification: {key}")
