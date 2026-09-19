"""Android provider, bounded ADB transport and governed M13 capabilities."""

from agentx.capabilities.android.provider import ANDROID_ADB_PROVIDER_ID, AndroidProvider
from agentx.capabilities.android.runtime import (
    ANDROID_CAPABILITY_IDENTITIES,
    AndroidActionCapability,
    AndroidActionParams,
    AndroidDriver,
    AndroidNavigation,
    AndroidOperation,
    AndroidScreenCapture,
    AndroidVerificationSpec,
    build_android_capabilities,
)
from agentx.capabilities.android.transport import (
    AdbCommandResult,
    AdbDeviceRecord,
    AdbDeviceState,
    AdbTransport,
    SubprocessAdbRunner,
)
from agentx.capabilities.android.ui import (
    AndroidBounds,
    AndroidTargetSelector,
    AndroidUiNode,
    AndroidUiTree,
    AndroidUiValidationError,
    parse_android_ui_tree,
    resolve_android_target,
)

__all__ = [
    "ANDROID_ADB_PROVIDER_ID",
    "ANDROID_CAPABILITY_IDENTITIES",
    "AdbCommandResult",
    "AdbDeviceRecord",
    "AdbDeviceState",
    "AdbTransport",
    "AndroidActionCapability",
    "AndroidActionParams",
    "AndroidBounds",
    "AndroidDriver",
    "AndroidNavigation",
    "AndroidOperation",
    "AndroidProvider",
    "AndroidScreenCapture",
    "AndroidTargetSelector",
    "AndroidUiNode",
    "AndroidUiTree",
    "AndroidUiValidationError",
    "AndroidVerificationSpec",
    "SubprocessAdbRunner",
    "build_android_capabilities",
    "parse_android_ui_tree",
    "resolve_android_target",
]
