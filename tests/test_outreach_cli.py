"""The outreach CLI end to end, each test in its own config and data folders."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "skills" / "recruiter-outreach" / "scripts" / "outreach.py"


def env_for(tmp_path, with_config=True):
    cfg, data = tmp_path / "config", tmp_path / "data"
    cfg.mkdir()
    data.mkdir()
    if with_config:
        shutil.copy(ROOT / "config" / "outreach.example.md", cfg / "outreach.md")
    env = dict(os.environ, JOBAGENT_CONFIG_DIR=str(cfg), JOBAGENT_DATA_DIR=str(data),
               PYTHONIOENCODING="utf-8")
    return env


def cli(env, *args):
    return subprocess.run([sys.executable, str(CLI), *args], env=env, capture_output=True,
                          text=True, encoding="utf-8")


def test_cli_refuses_without_outreach_md(tmp_path):
    r = cli(env_for(tmp_path, with_config=False), "status")
    assert r.returncode == 2
    assert "outreach.md" in r.stderr


def test_cli_refuses_tracker_inside_jobagent(tmp_path):
    env = env_for(tmp_path)
    env["JOBAGENT_DATA_DIR"] = str(ROOT)
    r = cli(env, "status")
    assert r.returncode == 2
    assert "JOBAGENT_DATA_DIR" in r.stderr


def test_cli_round_trip(tmp_path):
    env = env_for(tmp_path)
    r = cli(env, "add-contact", "--company", "Example Corp", "--name", "Example Recruiter", "--title",
            "Technical Recruiter", "--profile-url", "https://www.linkedin.com/in/example-recruiter-1",
            "--group", "target")
    assert r.returncode == 0, r.stderr
    cid = json.loads(r.stdout)["id"]
    assert cli(env, "verify", "--id", str(cid)).returncode == 0
    due = json.loads(cli(env, "due").stdout)
    assert [i["contact_id"] for i in due["new_invites"]] == [cid]
    note = tmp_path / "note.txt"
    note.write_text("Hi there, I enjoyed your post about the new Rio office.", encoding="utf-8")
    r = cli(env, "draft", "--contact", str(cid), "--kind", "invite_note", "--body-file", str(note))
    assert r.returncode == 0, r.stderr
    mid = json.loads(r.stdout)["id"]
    r = cli(env, "batch")
    assert r.returncode == 0, r.stderr
    token = r.stdout.splitlines()[0].split("token:")[1].strip()
    assert "Example Corp" in r.stdout
    for args in (("approve", "--token", token, "--ids", str(mid)), ("claim", "--id", str(mid)),
                 ("mark-sent", "--id", str(mid))):
        r = cli(env, *args)
        assert r.returncode == 0, r.stderr
    r = cli(env, "claim", "--id", str(mid))
    assert r.returncode == 2
    assert f"message {mid}" in r.stderr
    assert "Example Recruiter" not in r.stderr
    status = json.loads(cli(env, "status").stdout)
    assert status["contacts"] == {"invited": 1}
    assert status["today_sent"] == 1


def test_cli_prints_non_ascii_names_without_env(tmp_path):
    env = env_for(tmp_path)
    env.pop("PYTHONIOENCODING", None)
    r = cli(env, "add-contact", "--company", "Example Corp", "--name", "João Conceição",
            "--title", "Recrutadora", "--profile-url", "https://www.linkedin.com/in/example-recruiter-2",
            "--group", "target")
    assert r.returncode == 0, r.stderr
    cid = json.loads(r.stdout)["id"]
    cli(env, "verify", "--id", str(cid))
    r = cli(env, "due")
    assert r.returncode == 0, r.stderr
    assert "João" in r.stdout
