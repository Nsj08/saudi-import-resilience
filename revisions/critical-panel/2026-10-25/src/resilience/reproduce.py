#!/usr/bin/env python3
"""Run or verify the offline public Stage 2 pipeline in an isolated copy."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def sha(path: Path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def raw_hashes(root: Path):
    base=root/"data/raw"
    return {p.relative_to(root).as_posix():sha(p) for p in sorted(base.rglob("*")) if p.is_file()}


def call(root: Path, script: str, command: str):
    argv=[sys.executable,str(root/"src/resilience"/script),command,"--root",str(root)]
    subprocess.run(argv,cwd=root,check=True,env={**os.environ,"MPLBACKEND":"Agg"})


def pipeline(root: Path):
    """Run only cached raw -> cleaning -> accepted analysis -> report; never fetch."""
    # data.py verify rebuilds cleaned tables and the quality report, then enforces the quality gate.
    call(root,"data.py","verify")
    call(root,"analysis.py","run")
    call(root,"report.py","run")


def output_files(root: Path):
    roots=(root/"data/processed/stage2",root/"results/stage2/analysis",root/"results/stage2/figures",root/"results/stage2/tables")
    result={}
    for folder in roots:
        for p in sorted(folder.rglob("*")):
            if p.is_file(): result[p.relative_to(root).as_posix()]=p
    for rel in ("results/stage2/data_quality.md",):
        p=root/rel
        if p.is_file(): result[rel]=p
    return result


def copy_public_runtime(root: Path, destination: Path):
    """Copy only public inputs and runtime files required by the pipeline."""
    files=(
        "PROJECT_RULES.md",
        "decisions/research_protocol_2026-10-25.md",
        "decisions/stage2_execution_addendum.md",
        "decisions/stage2_analysis_clarifications.md",
        "decisions/scenario_assumption_register.csv",
        "decisions/validation_matrix.csv",
        "sources/raw_manifest.json",
        "supplementary/classification_basket.csv",
    )
    for rel in files:
        src=root/rel; dst=destination/rel; dst.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(src,dst)
    for rel in ("src/resilience", "data/raw", "data/processed/stage2", "results/stage2/analysis"):
        shutil.copytree(root/rel,destination/rel,ignore=shutil.ignore_patterns("__pycache__","*.pyc"))


def _compare(original: Path, reproduced: Path):
    a=output_files(original); b=output_files(reproduced)
    if set(a)!=set(b): raise ValueError("isolated reproduction output file set differs")
    numeric=[]; exact=[]; png_pixels=[]
    for rel in sorted(a):
        if rel.startswith("data/processed/stage2/") or rel.startswith("results/stage2/analysis/"):
            if a[rel].read_bytes()!=b[rel].read_bytes(): raise ValueError("numeric/analysis output differs: "+rel)
            numeric.append(rel)
        elif rel.endswith(".png"):
            if not png_pixels_equal(a[rel], b[rel]):
                raise ValueError("PNG RGBA pixel comparison differs: "+rel)
            png_pixels.append(rel)
        elif a[rel].read_bytes()!=b[rel].read_bytes():
            raise ValueError("report table/SVG/manifest bytes differ: "+rel)
        else:
            exact.append(rel)
    return {"numeric_files_byte_identical":len(numeric),"png_pixel_identical":len(png_pixels),"other_report_files_byte_identical":len(exact)}


def png_pixels_equal(left: Path, right: Path):
    """Compare decoded RGBA channel bytes, including RGB beneath zero alpha."""
    from PIL import Image
    with Image.open(left) as a, Image.open(right) as b:
        if a.size != b.size:
            return False
        return a.convert("RGBA").tobytes() == b.convert("RGBA").tobytes()


def verify(root: Path):
    root=root.resolve()
    raw_before=raw_hashes(root)
    # Check the preserved output manifests before constructing the replay.
    subprocess.run([sys.executable,str(root/"src/resilience/analysis.py"),"verify","--root",str(root)],cwd=root,check=True,env={**os.environ,"MPLBACKEND":"Agg"})
    subprocess.run([sys.executable,str(root/"src/resilience/report.py"),"verify","--root",str(root)],cwd=root,check=True,env={**os.environ,"MPLBACKEND":"Agg"})
    with tempfile.TemporaryDirectory(prefix="stage2-public-reproduction-") as tmp:
        parent=Path(tmp)
        # Use a public subtree whose runtime sees no private workspace or original absolute paths.
        replay=parent/"copy"
        replay.mkdir()
        copy_public_runtime(root,replay)
        if (replay/".agent-work").exists() or (replay/".git").exists():
            raise ValueError("isolated public replay contains private/coordination files")
        for rel in ("REPRODUCE.md", "supplementary/stage2_environment.json"):
            if str(root) in (root/rel).read_text(encoding="utf-8"):
                raise ValueError("public documentation/environment contains the source machine path: "+rel)
        pipeline(replay)
        # Verify manifests in the isolated tree and exact agreement with the accepted public outputs.
        subprocess.run([sys.executable,str(replay/"src/resilience/analysis.py"),"verify","--root",str(replay)],cwd=replay,check=True,env={**os.environ,"MPLBACKEND":"Agg"})
        subprocess.run([sys.executable,str(replay/"src/resilience/report.py"),"verify","--root",str(replay)],cwd=replay,check=True,env={**os.environ,"MPLBACKEND":"Agg"})
        fidelity=_compare(root,replay)
        raw_replay=raw_hashes(replay)
        if raw_replay!=raw_before: raise ValueError("isolated reproduction changed one or more raw input files")
    if raw_hashes(root)!=raw_before: raise ValueError("verification changed original raw inputs")
    print(json.dumps({"verification":"passed","isolated_public_copy":True,"raw_files_unchanged":len(raw_before),**fidelity},sort_keys=True))


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("command",choices=("run","verify"))
    ap.add_argument("--root",default=str(Path(__file__).resolve().parents[2]))
    args=ap.parse_args(); root=Path(args.root).resolve()
    if args.command=="run":
        before=raw_hashes(root); pipeline(root)
        if raw_hashes(root)!=before: raise ValueError("pipeline changed one or more raw input files")
        print(json.dumps({"run":"passed","offline":True,"raw_files_unchanged":len(before)},sort_keys=True))
    else: verify(root)


if __name__=="__main__": main()
