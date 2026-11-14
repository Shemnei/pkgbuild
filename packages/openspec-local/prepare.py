"""Replace audited upstream reporting and software-installation boundaries."""

import hashlib
import json
import sys
from pathlib import Path

BOUNDARIES = {
    "src/telemetry/index.ts": (
        "9b129faf59bf404a47f2dc3a00fd04cc41ade0339b0d10ada7464e36dbe77807",
        "telemetry.ts",
    ),
    "src/core/version-check.ts": (
        "aecc94d62dd72f7a31d8fc292cdb87a03325f00f6a2f9bedd70c4149f6dee8c0",
        "version-check.ts",
    ),
    "src/commands/install-standalone.ts": (
        "34b14be0c1f2737a6c398499a7edef6dff2458994dd2274d35821207fb2964d0",
        "install-standalone.ts",
    ),
    "src/core/standalone-tip.ts": (
        "9530fb0c4ef37daee8271a262164bf9139c913ec89d552900f862e8d24f3c82a",
        "standalone-tip.ts",
    ),
}

TRANSFORMS = {
    "src/core/github-copilot/cloud-agent.ts": (
        "00d3eb340ce392f53cce1a890a64ff9672c45092332a685824f55f88925dcb83",
        [
            (
                "${generateCopilotSetupStepsBody()}`;",
                "${generateCopilotSetupStepsBody()}`.replace(\n"
                "    '      - name: Install OpenSpec CLI\\n        run: npm install -g @fission-ai/openspec',\n"
                "    '      - name: Require pre-provisioned OpenSpec CLI\\n        run: command -v openspec'\n"
                "  );",
            ),
            (
                "return generateCopilotAgentFileBody(true);",
                "return generateCopilotAgentFileBody(true).replace(\n"
                "    'If it is unavailable, install it with `npm install -g @fission-ai/openspec`.',\n"
                "    'If it is unavailable, ask the administrator to provision it through the system package manager (pacman on Arch). Do not install or upgrade software yourself.'\n"
                "  );",
            ),
            (
                "return [generateCopilotSetupStepsBody()];",
                "return [\n"
                "      generateCopilotSetupStepsBody(),\n"
                "      `# ${OPENSPEC_MANAGED_MARKER}\\n\\n${generateCopilotSetupStepsBody()}`,\n"
                "      generateCopilotSetupSteps().replace(/^# [^\\n]+\\n\\n/, ''),\n"
                "    ];",
            ),
            (
                "    generateCopilotAgentFileBody(),",
                "    generateCopilotAgentFileBody(),\n"
                "    generateCopilotAgentFileBody(true),\n"
                "    generateCopilotAgentFile().replace(`<!-- ${OPENSPEC_MANAGED_MARKER} -->\\n\\n`, ''),",
            ),
        ],
    ),
    "test/core/github-copilot-cloud-agent.test.ts": (
        "d450c97b0b9f0192060ec894c4e71b1a1687cd648c23eb7f96587ff0c93acd42",
        [
            (
                "expect.objectContaining({ run: 'npm install -g @fission-ai/openspec' })",
                "expect.objectContaining({ run: 'command -v openspec' })",
            ),
            (
                "expect(agentContent).toContain('install it with `npm install -g @fission-ai/openspec`');",
                "expect(agentContent).toContain('ask the administrator to provision it');",
            ),
            (
                "'You are a specialized agent for managing OpenSpec workflows. Before using the `openspec` CLI, run `openspec --version`. If it is unavailable, install it with `npm install -g @fission-ai/openspec`.',",
                "'You are a specialized agent for managing OpenSpec workflows. Before using the `openspec` CLI, run `openspec --version`. If it is unavailable, ask the administrator to provision it through the system package manager (pacman on Arch). Do not install or upgrade software yourself.',",
            ),
        ],
    ),
}


def prepare(root, replacements=None):
    replacements = replacements or Path(__file__).resolve().parent
    edits = {}
    manifest_path = root / "package.json"
    manifest = json.loads(manifest_path.read_text())
    if manifest.pop("packageManager", None) != "pnpm@10.34.5":
        raise ValueError("Upstream package manager pin changed")
    # Use Arch's pnpm rather than letting pnpm download and run its own replacement.
    edits[manifest_path] = (json.dumps(manifest, indent=2) + "\n").encode()
    for relative, (expected, replacement) in BOUNDARIES.items():
        target = root / relative
        if hashlib.sha256(target.read_bytes()).hexdigest() != expected:
            raise ValueError(f"Upstream policy boundary changed: {relative}")
        edits[target] = (replacements / replacement).read_bytes()
    for relative, (expected, transforms) in TRANSFORMS.items():
        target = root / relative
        content = target.read_bytes()
        if hashlib.sha256(content).hexdigest() != expected:
            raise ValueError(f"Upstream policy boundary changed: {relative}")
        text = content.decode()
        for before, after in transforms:
            if text.count(before) != 1:
                raise ValueError(f"Policy transform is not unique: {relative}")
            text = text.replace(before, after)
        edits[target] = text.encode()
    # Validate all inputs before writing any of them.
    for target, content in edits.items():
        target.write_bytes(content)


if __name__ == "__main__":
    prepare(Path(sys.argv[1]))
