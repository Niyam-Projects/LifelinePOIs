import subprocess
import argparse
import sys
import os
import yaml
from pathlib import Path

def run_command(cmd, env=None):
    print(f"\n[EXEC] {' '.join(cmd)}")
    
    # Intercept and rewrite incoming legacy 'marimo run' command arrays
    if cmd[0] == "marimo":
        try:
            # e.g., ['marimo', 'run', 'flows/00_setup.py', '--', '--region', 'puerto-rico']
            if len(cmd) > 2 and cmd[1] == "run":
                file_path = cmd[2]
                args_after_sep = []
                
                # Strip out the legacy '--' separator if present
                if "--" in cmd:
                    idx = cmd.index("--")
                    args_after_sep = cmd[idx+1:]
                elif len(cmd) > 3:
                    # Catch cases where '--' wasn't used but arguments exist
                    args_after_sep = cmd[3:]
                    
                cmd = ["uv", "run", "python", file_path] + args_after_sep
            else:
                # Fallback for any other arbitrary marimo commands
                cmd = ["uv", "run", "python"] + cmd[1:]
        except Exception:
            cmd = ["uv", "run", "python"] + cmd[1:]
            
    result = subprocess.run(cmd, env=env)
    if result.returncode != 0:
        print(f"\n[ERROR] Command failed with exit code {result.returncode}: {' '.join(cmd)}")
        sys.exit(result.returncode)

def load_config(config_path):
    with open(config_path, 'r') as f:
        return yaml.safe_load(f)

def main():
    parser = argparse.ArgumentParser(description="Orchestrate LifelinePOI processing pipeline.")
    parser.add_argument("--config", default="config.lifeline.yaml", help="Path to config file")
    parser.add_argument("--area", required=True, help="Area name from config (e.g., florida)")
    args = parser.parse_args()

    config = load_config(args.config)
    
    # Validate area exists in config
    available_areas = [a['name'] for a in config.get('areas', [])]
    if args.area not in available_areas:
        print(f"Error: Area '{args.area}' not found in config. Available: {available_areas}")
        sys.exit(1)
    
    print(f"Starting pipeline for area: {args.area}")

    # Build env with LIFELINE_AREA_NAME so that LifelineConfig.from_yaml()
    # populates cfg.aoi — enabling bbox + state_code filtering of national
    # datasets (EPA FRS, HIFLD, CMS) in silver conflation.
    area_env = {**os.environ, "LIFELINE_AREA_NAME": args.area}

    # 1. Setup (Overture addresses)
    run_command(["uv", "run", "python", "flows/00_setup.py", "--region", args.area])

    # 2. Ingest (OSM + Federal)
    run_command(["uv", "run", "python", "flows/01_ingest.py", "--area", args.area])

    # 3. Silver Conflation
    run_command(["uv", "run", "python", "flows/02_silver_conflation.py"], env=area_env)

    # 4. Campus Collapse
    run_command(["uv", "run", "python", "flows/02b_campus_collapse.py"], env=area_env)

    # 5. GERSite Bridge
    run_command(["uv", "run", "python", "flows/03_gersite_bridge.py"], env=area_env)

    # 6. Gold Production
    run_command(["uv", "run", "python", "flows/04_gold_production.py"], env=area_env)

    print(f"\n[SUCCESS] Pipeline completed successfully for {args.area}")

if __name__ == "__main__":
    main()
