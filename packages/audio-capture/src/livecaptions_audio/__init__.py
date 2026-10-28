"""Device adapters, separate from recognition and application interfaces."""

from .wasapi import WasapiRecording, list_devices

__all__ = ["WasapiRecording", "list_devices"]
