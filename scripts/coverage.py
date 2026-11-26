from pathlib import Path
from fpdb.planner import load_config, completed_case_ids, make_case, _pairs
from fpdb.runtime_compat import local_ca_supported

root = Path(".")
config = load_config(root)
completed = completed_case_ids(root)

total = done = 0

for target in config["targets"]:
    if not target.get("enabled"):
        continue

    for runtime, client in _pairs(
        target, "full", config["execution"]["active_runtime_count"]
    ):
        for mode in config.get("tls_verify_modes", ["default"]):
            if mode == "default" and not local_ca_supported(target["id"], runtime):
                continue

            case = make_case(
                target, runtime, client, verify=False if mode == "disabled" else None
            )
            total += 1
            done += case["id"] in completed

print(f"已完成: {done}")
print(f"全部组合: {total}")
print(f"完成率: {done / total * 100:.2f}%")
