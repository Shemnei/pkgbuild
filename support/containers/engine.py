"""Docker/Podman transport: copied inputs, default sandbox, scoped cleanup."""

import hashlib
import json
import shlex
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path


def command(*arguments, capture=False):
    print(f"[host] {shlex.join(map(str, arguments))}", flush=True)
    return subprocess.run(
        list(map(str, arguments)), text=True, capture_output=capture, check=True
    ).stdout


def logged(arguments, path):
    print(f"[host] {shlex.join(map(str, arguments))}", flush=True)
    with path.open("w") as log:
        process = subprocess.Popen(
            list(map(str, arguments)), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
        )
        try:
            for line in process.stdout:
                print(line, end="", flush=True)
                log.write(line)
            return process.wait()
        finally:
            process.stdout.close()
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


class Engine:
    def __init__(self, name=None, require_rootless=False):
        self.name = name or ("podman" if shutil.which("podman") else "docker")
        if self.name not in {"docker", "podman"} or shutil.which(self.name) is None:
            raise ValueError("Install Docker or Podman; CONTAINER_ENGINE must name one of them")
        if self.name == "docker":
            security = json.loads(
                command(self.name, "info", "--format", "{{json .SecurityOptions}}", capture=True)
            )
            self.rootless = "name=rootless" in security
        else:
            value = command(
                self.name, "info", "--format", "{{.Host.Security.Rootless}}", capture=True
            )
            self.rootless = value.strip() == "true"
        if not self.rootless:
            if require_rootless:
                raise ValueError("The selected daemon is not rootless")
            print(
                "WARNING: rootful container engine access is effectively host-root access",
                flush=True,
            )

    def image(self, context, lock, target, output):
        inputs = {name: context / name for name in ("Containerfile", "__init__.py", "worker.py")}
        inputs.update(
            {
                name: context.parent / name
                for name in ("recipes.py", "artifacts.py", "diagnostics.py")
            }
        )
        digest = hashlib.sha256(json.dumps(lock, sort_keys=True).encode())
        for name, path in sorted(inputs.items()):
            digest.update(name.encode())
            digest.update(path.read_bytes())
        tag = f"pkgbuild-validation:{digest.hexdigest()[:20]}-{target}"
        exists = subprocess.run(
            [self.name, "image", "inspect", tag], capture_output=True, text=True, check=False
        )
        if exists.returncode:
            arguments = [
                self.name,
                "build",
                "--pull=always" if self.name == "podman" else "--pull",
                "--platform",
                lock["platform"],
                "--target",
                target,
                "--tag",
                tag,
                "--build-arg",
                f"BASE_IMAGE={lock['base_image']}",
                "--build-arg",
                f"ARCH_SNAPSHOT={lock['arch_snapshot'].replace('-', '/')}",
            ]
            # Send only reviewed image inputs, not the repository or host caches.
            with tempfile.TemporaryDirectory(prefix="pkgbuild-image-") as temporary:
                for name, path in inputs.items():
                    shutil.copyfile(path, Path(temporary) / name)
                status = logged(
                    [*arguments, "--file", str(Path(temporary) / "Containerfile"), temporary],
                    output / f"image-{target}.log",
                )
            if status:
                raise RuntimeError(f"Container image build failed ({status}); see {output}")
        identity = command(
            self.name, "image", "inspect", "--format", "{{.Id}}", tag, capture=True
        ).strip()
        return {"reference": tag, "id": identity}

    def create_arguments(self, image, phase, package, platform):
        arguments = [
            self.name,
            "create",
            "--init",
            "--platform",
            platform,
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            "2048",
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,mode=1777",
        ]
        if phase in {"check", "metadata-inputs", "srcinfo", "lint", "smoke"}:
            arguments.extend(["--network", "none"])
        arguments.extend([image, phase])
        if package:
            arguments.append(package)
        return arguments

    def job(self, image, phase, package, platform, snapshot, archives, output, commit=None):
        output.mkdir(parents=True)
        container = command(
            *self.create_arguments(image, phase, package, platform), capture=True
        ).strip()
        if not container or any(character not in "0123456789abcdef" for character in container):
            raise ValueError(f"Unexpected container identifier: {container!r}")
        started = False
        status = None
        try:
            command(self.name, "cp", f"{snapshot}/.", f"{container}:/input")
            for name, directory in archives.items():
                command(self.name, "cp", directory, f"{container}:/archives/{name}")
            started = True
            status = logged([self.name, "start", "--attach", container], output / "console.log")
            return status
        finally:
            if started:
                try:
                    command(self.name, "cp", f"{container}:/output/.", output)
                except (OSError, subprocess.CalledProcessError):
                    print(f"Artifact export failed; preserving container {container}", flush=True)
                    raise
            try:
                if status == 0 and commit:
                    command(self.name, "commit", container, commit)
            finally:
                command(self.name, "rm", "--force", container)

    def runtime_tag(self):
        return f"pkgbuild-validation:installed-{uuid.uuid4().hex}"

    def remove_image(self, image):
        command(self.name, "image", "rm", image)
