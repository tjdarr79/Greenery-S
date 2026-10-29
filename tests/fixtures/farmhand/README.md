# farmhand fixtures (captured on the owner's farm)

Real replies from farmhand 2.0.26 at 192.168.200.200:3001, for unit tests and
reference. UUIDs (controller_id, recipe ids) are replaced with placeholders.

| File | What it is |
|---|---|
| `camera_dump_events.json` | Output of `tools/farmhand/dump_cameras.py`, 2026-10-16. Every message seen on `/farm-monitoring` and `/farm-images` after sending `read_cameras` and `get_last_images_local_only`. Image strings are shortened to their length. |
| `farm_images_sample.sse` | Six real `/farm-images` events in SSE wire format (`data: {...}` + blank line), one per camera. **Images are 320x180 thumbnails** of the real photos (real ones: 1296x730 JPEG, 550-1050 KB, about 1.0-1.4 M base64 chars each). Use to test the reader, dedupe and MQTT publish. |
| `farm_monitoring_camera_replies.sse` | The two `/farm-monitoring` replies: `read_cameras` (6 camera module ids) and `get_last_images_local_only` ("SUCCESS"). |
| `camera_aliases.json` | `get_farm_info.response.camera_aliases`: alias -> module, display name, zone, `rotation` (all 0 on this farm). |
| `module_aliases.json` | `get_farm_info.response.module_aliases`: every named input/output, e.g. dosing tank levels `nutrient_a_level` = `E831CDC7E680/digital_1`. |
| `farmhand_replies_2026-10-15.json` | Full replies to `get_farm_info` (132 live settings incl. `image_cycle_time` 10800 and the six `*_24hr_enable` flags), `get_version`, `get_current_mode`, `read_modules`. |

## Camera event format
```
/farm-images:      {"camera": "<module id>", "image": "<base64 JPEG>", "last_update": <epoch seconds, float>}
/farm-monitoring:  {"response_to": "<command>", "response": ..., "error_message": "", "controller_id": "...", "request_id": ""}
```

## Camera ids (this farm)
| Alias | Module | Name |
|---|---|---|
| nursery_top | E45F01CD2C40 | Nursery Top |
| nursery_bottom | E45F01CD2C43 | Nursery Bottom |
| cultivation_left | E45F01CD2C8E | Cultivation Left |
| cultivation_left_center | E45F01CD2C73 | Cultivation Left Middle |
| cultivation_right_center | E45F01CD2C6D | Cultivation Right Middle |
| cultivation_right | E45F01CD2C88 | Cultivation Right |

Photos arrived about 2 s apart after the request; all six last_update values were
within 16 s of each other (one photo cycle).
