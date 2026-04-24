from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256


@dataclass
class SkillManifest:
    name: str
    version: str
    permissions: list[str]
    required_secrets: list[str]
    body: str


@dataclass
class SkillRegistry:
    _skills: dict[str, SkillManifest] = field(default_factory=dict)
    _signatures: dict[str, str] = field(default_factory=dict)

    def add(self, manifest: SkillManifest) -> str:
        signature = self.sign(manifest)
        key = f"{manifest.name}:{manifest.version}"
        self._skills[key] = manifest
        self._signatures[key] = signature
        return signature

    def sign(self, manifest: SkillManifest) -> str:
        perms = ",".join(sorted(manifest.permissions))
        payload = f"{manifest.name}|{manifest.version}|{perms}|{manifest.body}"
        return sha256(payload.encode("utf-8")).hexdigest()

    def verify(self, manifest: SkillManifest, signature: str) -> bool:
        return self.sign(manifest) == signature


@dataclass
class SkillLearner:
    """Hermes-driven self-improvement shim."""

    def refine(self, manifest: SkillManifest, trajectory_success_rate: float) -> SkillManifest:
        if trajectory_success_rate >= 0.9:
            return manifest
        improved_body = manifest.body + "\n# refinement: add clearer precondition checks"
        return SkillManifest(
            name=manifest.name,
            version=manifest.version,
            permissions=manifest.permissions,
            required_secrets=manifest.required_secrets,
            body=improved_body,
        )


@dataclass
class RegressionGate:
    min_success_rate: float = 0.8

    def can_promote(self, before: float, after: float) -> bool:
        return after >= self.min_success_rate and after >= before
