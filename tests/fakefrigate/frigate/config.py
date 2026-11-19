"""Minimal stand-in for Frigate's pydantic model: structural checks only."""
class ValidationError(Exception):
    def __init__(self, errs): self._e = errs; super().__init__("invalid")
    def errors(self): return self._e

class FrigateConfig:
    @staticmethod
    def model_validate(data):
        errs = []
        streams = data.get("go2rtc", {}).get("streams", {})
        for name, cam in data.get("cameras", {}).items():
            roles = [r for i in cam["ffmpeg"]["inputs"] for r in i["roles"]]
            if sorted(roles) != ["detect", "record"]:
                errs.append({"loc": ("cameras", name, "ffmpeg", "inputs"), "type": "roles"})
            for i in cam["ffmpeg"]["inputs"]:
                s = i["path"].rsplit("/", 1)[-1]
                if i["path"].startswith("rtsp://127.0.0.1:8554/") and s not in streams:
                    errs.append({"loc": ("cameras", name, "ffmpeg", "inputs"), "type": "missing_stream"})
            for v in cam.get("live", {}).get("streams", {}).values():
                if v not in streams:
                    errs.append({"loc": ("cameras", name, "live", "streams"), "type": "missing_stream"})
            d = cam.get("detect", {})
            if d and not (d.get("width") and d.get("height")):
                errs.append({"loc": ("cameras", name, "detect"), "type": "dims"})
        if errs: raise ValidationError(errs)
