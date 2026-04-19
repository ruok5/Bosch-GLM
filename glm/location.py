"""macOS CoreLocation lookup. Optional — degrades to None if unavailable.

The first call from a given binary triggers a system permission prompt. Until
the user grants Location Services to the calling process, this module returns
None within the timeout. We never raise — callers can ignore failures and
proceed without geolocation.
"""
from __future__ import annotations

import asyncio
import logging
import math
from typing import Optional

from .store import LocationFix

logger = logging.getLogger(__name__)


def _sync_get_fix(timeout_s: float = 4.0) -> Optional[LocationFix]:
    """Blocking CoreLocation lookup. Run via run_in_executor for asyncio code."""
    try:
        from CoreLocation import CLLocationManager  # type: ignore
        from Foundation import NSObject, NSRunLoop, NSDate  # type: ignore
    except ImportError:
        logger.debug("pyobjc-framework-CoreLocation not installed")
        return None

    class _Delegate(NSObject):
        def init(self):
            self = NSObject.init(self)
            if self is None:
                return None
            self.fix = None
            self.failed = False
            return self

        def locationManager_didUpdateLocations_(self, _manager, locations):
            if locations and len(locations) > 0:
                loc = locations[-1]
                coord = loc.coordinate()
                self.fix = LocationFix(
                    latitude=float(coord.latitude),
                    longitude=float(coord.longitude),
                    accuracy_m=float(loc.horizontalAccuracy()),
                )

        def locationManager_didFailWithError_(self, _manager, error):
            logger.debug("CoreLocation error: %s", error)
            self.failed = True

    manager = CLLocationManager.alloc().init()
    if not CLLocationManager.locationServicesEnabled():
        logger.debug("Location Services disabled at the system level")
        return None
    delegate = _Delegate.alloc().init()
    manager.setDelegate_(delegate)
    # Best-available: kCLLocationAccuracyBest = -1.0 (constant)
    try:
        manager.setDesiredAccuracy_(-1.0)
    except Exception:
        pass
    manager.startUpdatingLocation()

    runloop = NSRunLoop.currentRunLoop()
    deadline = NSDate.dateWithTimeIntervalSinceNow_(timeout_s)
    while delegate.fix is None and not delegate.failed:
        # Pump the run loop briefly so delegate callbacks fire
        runloop.runMode_beforeDate_("NSDefaultRunLoopMode",
                                     NSDate.dateWithTimeIntervalSinceNow_(0.1))
        if NSDate.date().compare_(deadline) >= 0:
            break

    manager.stopUpdatingLocation()
    return delegate.fix


async def get_fix(timeout_s: float = 4.0) -> Optional[LocationFix]:
    """Non-blocking wrapper. Safe to call from asyncio code."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _sync_get_fix, timeout_s)


def haversine_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Great-circle distance in meters between two (lat, lon) points."""
    lat1, lon1 = math.radians(a[0]), math.radians(a[1])
    lat2, lon2 = math.radians(b[0]), math.radians(b[1])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * 6_371_000 * math.asin(math.sqrt(h))
