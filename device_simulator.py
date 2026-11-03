"""Synthetic Android device profiles for isolated tests only.

These identifiers are deliberately non-routable placeholders. They must never
be presented to Google or another third-party service as real device identity.
"""
from dataclasses import asdict, dataclass
from uuid import uuid4
import hashlib


@dataclass(frozen=True)
class DeviceProfile:
    model: str
    android_version: str
    imei: str
    android_id: str
    session_id: str
    user_agent: str
    environment: str = "LOCAL_TEST_ONLY"

    def to_dict(self):
        return asdict(self)


def create_test_profile(seed: str | None = None) -> DeviceProfile:
    """Create a synthetic Pixel profile for mock services and unit tests."""
    session_id = str(uuid4())
    digest = hashlib.sha256((seed or session_id).encode("utf-8")).hexdigest()
    # Explicit TEST prefixes make these values visibly synthetic, not valid IDs.
    return DeviceProfile(
        model="Pixel 10 Pro (simulated)",
        android_version="16 (test profile)",
        imei="TEST-IMEI-" + digest[:12].upper(),
        android_id="TEST-ANDROID-" + digest[12:28],
        session_id=session_id,
        user_agent=("PixelGeminiTest/1.0 (Linux; Android 16; Pixel 10 Pro; "
                    "isolated-test-profile)"),
    )
