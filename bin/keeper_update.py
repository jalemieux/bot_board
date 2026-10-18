"""Managed keeper releases. Standard library only; the installed supervisor stays outside releases."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
from repository_auth import git_environment


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args], stderr=subprocess.STDOUT,
                                   timeout=60, env=git_environment(os.environ)).decode().strip()


def save(path, data):
    temp = path.with_suffix(".new")
    temp.write_text(json.dumps(data, indent=2) + "\n")
    os.replace(temp, path)


def switch(root, revision, name="src"):
    link = root / (name + ".new")
    link.unlink(missing_ok=True)
    link.symlink_to(root / "releases" / revision, target_is_directory=True)
    os.replace(link, root / name)


def validate(path):
    # Compile without creating bytecode in an otherwise clean checkout.
    for file in (path / "bin").glob("*.py"):
        compile(file.read_bytes(), str(file), "exec")
    compile((path / "bin/keeper").read_bytes(), str(path / "bin/keeper"), "exec")
    subprocess.run(["bash", "-n", str(path / "bin/bot")], check=True, capture_output=True, timeout=15)
    for name in ("bot", "keeper"):
        if not (path / "bin" / name).is_file():
            raise RuntimeError("release is missing bin/" + name)


class Updates:
    def __init__(self, root):
        self.root = Path(root).absolute()
        self.path = self.root / "update.json"

    def read(self):
        return json.loads(self.path.read_text())

    def report(self):
        state = self.read()
        return {k: state.get(k, "") for k in ("installed", "available", "previous", "checked_at", "result", "error", "ref")}

    def stage(self, revision):
        release = self.root / "releases" / revision
        if release.exists():
            if git(release, "status", "--porcelain", "--untracked-files=all"):
                raise RuntimeError("release checkout is dirty; leaving it untouched")
            validate(release)
            return
        temp = self.root / "releases" / (revision + ".staging")
        shutil.rmtree(temp, ignore_errors=True)
        temp.parent.mkdir(exist_ok=True)
        try:
            git(self.root / "source", "clone", "--no-hardlinks", "--no-checkout", ".", str(temp))
            git(temp, "checkout", "--detach", revision)
            validate(temp)
            os.replace(temp, release)
        finally:
            shutil.rmtree(temp, ignore_errors=True)

    def check(self, ref="", enabled=True, force=False):
        """Stage and atomically select an update; supervisor starts it and confirms readiness."""
        with (self.root / "update.lock").open("w") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return False
            state = self.read()
            if state.get("pending"):
                return False
            if not force and time.time() - state.get("checked", 0) < 300:
                return False
            state.update(checked=time.time(), checked_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
            try:
                current = self.root / "src"
                if git(current, "status", "--porcelain", "--untracked-files=all"):
                    raise RuntimeError("installed checkout is dirty; leaving it untouched")
                source = self.root / "source"
                if git(source, "status", "--porcelain", "--untracked-files=all"):
                    raise RuntimeError("update source checkout is dirty; leaving it untouched")
                target = ref or state["ref"]
                git(source, "fetch", "--no-tags", "origin", target)
                revision = git(source, "rev-parse", "FETCH_HEAD^{commit}")
                state["available"] = revision
                if not enabled:
                    state["result"] = "disabled"
                elif revision == state["installed"]:
                    state.update(result="current", error="")
                elif revision == state.get("failed") and not force:
                    state["result"] = "blocked: failed release (run keeper update to retry)"
                else:
                    self.stage(revision)
                    state.update(previous=state["installed"], installed=revision, pending=True,
                                 result="starting update", error="")
                    save(self.path, state)  # Persist rollback information before switching.
                    switch(self.root, revision)
                    return True
            except (OSError, RuntimeError, SyntaxError, subprocess.SubprocessError) as exc:
                # Do not expose remote URLs or git output, which can contain credentials.
                state.update(result="update failed", error=f"{type(exc).__name__}: check source access, checkout cleanliness and release validation")
                if isinstance(exc, RuntimeError):
                    state["error"] = str(exc)
            save(self.path, state)
            return False

    def ready(self, revision):
        with (self.root / "update.lock").open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            state = self.read()
            if state.get("pending") and state["installed"] == revision:
                switch(self.root, state["installed"], "launcher")
                state.update(pending=False, result="updated", error="")
                save(self.path, state)

    def rollback(self, reason):
        with (self.root / "update.lock").open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            state = self.read()
            previous = state.get("previous")
            if not previous:
                raise RuntimeError("no previous release available")
            if git(self.root / "releases" / previous, "status", "--porcelain", "--untracked-files=all"):
                raise RuntimeError("previous release is dirty; restore it before rollback")
            validate(self.root / "releases" / previous)
            switch(self.root, previous)
            switch(self.root, previous, "launcher")
            state.update(failed=state["installed"], installed=previous, pending=False,
                         result="rolled back", error=reason)
            save(self.path, state)

def initialize(root, repo, ref):
    root = Path(root).absolute()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (root / "install.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        owned_source = not (root / "source").exists()
        had_initial = (root / "initial-checkout").exists()
        try:
            return _initialize(root, repo, ref)
        except Exception:
            if owned_source and not (root / "update.json").exists():
                shutil.rmtree(root / "source", ignore_errors=True)
                initial = root / "initial-checkout"
                if not had_initial and initial.exists():
                    if (root / "src").is_symlink():
                        (root / "src").unlink()
                    initial.rename(root / "src")
            raise


def _initialize(root, repo, ref):
    manager = Updates(root)
    if manager.path.exists():
        return manager
    source = root / "source"
    src = root / "src"
    if source.exists():
        raise RuntimeError("source already exists without update.json; inspect it before retrying")
    if src.exists():
        if src.is_symlink() or git(src, "status", "--porcelain", "--untracked-files=all"):
            raise RuntimeError("src must be a clean managed checkout")
        git(src, "clone", "--no-hardlinks", ".", str(source))
        git(source, "remote", "set-url", "origin", repo)
        revision = git(src, "rev-parse", "HEAD")
    else:
        root.joinpath("releases").mkdir(exist_ok=True)
        subprocess.run(["git", "clone", "--branch", ref, "--", repo, str(source)], check=True,
                       capture_output=True, timeout=120, env=git_environment(os.environ))
        revision = git(source, "rev-parse", "HEAD")
    previous = revision
    manager.stage(previous)
    git(source, "fetch", "--no-tags", "origin", ref)
    revision = git(source, "rev-parse", "FETCH_HEAD^{commit}")
    manager.stage(revision)
    if not (root / "releases" / revision / "bin/keeper_update.py").is_file():
        raise RuntimeError("target release predates managed updates; choose a newer install ref")
    shutil.copyfile(Path(__file__), root / "supervisor.py")
    shutil.copyfile(Path(__file__).with_name("repository_auth.py"), root / "repository_auth.py")
    if src.exists():
        # Preserve the original checkout as the initial rollback release, never delete it.
        src.rename(root / "initial-checkout")
    switch(root, revision)
    switch(root, previous, "launcher")
    save(manager.path, {"source": repo, "ref": ref, "installed": revision, "available": revision,
                        "previous": previous, "pending": previous != revision,
                        "result": "starting update" if previous != revision else "installed", "error": "", "checked": 0})
    return manager


def supervise(root):
    manager = Updates(root)
    lock = (manager.root / "supervisor.lock").open("w")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    child = None
    stopping = False

    def stop(signum, frame):
        nonlocal stopping
        stopping = True
        if child and child.poll() is None:
            child.terminate()  # Bots have their own sessions and must survive.
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    while not stopping:
        state = manager.read()
        if state.get("pending"):
            switch(manager.root, state["installed"])
        child = subprocess.Popen([sys.executable, str(manager.root / "src/bin/keeper"), "run"],
                                 env={**os.environ, "KEEPER_MANAGED_ROOT": str(manager.root), "KEEPER_SUPERVISED": "1"})
        deadline = time.monotonic() + 60
        while child.poll() is None and not stopping:
            if manager.read().get("pending") and time.monotonic() > deadline:
                child.terminate()
                try:
                    child.wait(5)
                except subprocess.TimeoutExpired:
                    child.kill(); child.wait()
                manager.rollback("new keeper did not report ready within 60 seconds")
                break
            time.sleep(1)
        if stopping:
            try:
                child.wait(5)
            except subprocess.TimeoutExpired:
                child.kill(); child.wait()
            break
        rc = child.wait()
        if rc != 75 and manager.read().get("pending"):
            manager.rollback(f"new keeper exited before its first successful report (exit {rc})")
        elif rc != 75:
            time.sleep(10)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("init", "supervise", "update", "rollback"))
    parser.add_argument("--root", required=True)
    parser.add_argument("--repo", default="https://github.com/jalemieux/bot_board.git")
    parser.add_argument("--ref", default="")
    args = parser.parse_args()
    if args.command == "init":
        initialize(args.root, args.repo, args.ref or "main")
    elif args.command == "supervise":
        supervise(args.root)
    else:
        manager = Updates(args.root)
        if args.command == "rollback":
            manager.rollback("manual rollback")
        else:
            manager.check(ref=args.ref, force=True)
        print(json.dumps(manager.report(), indent=2))
