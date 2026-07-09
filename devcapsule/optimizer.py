"""
AI-Based Optimization Layer
============================
Compares the hardware the capsule was captured on against the hardware
it is being restored onto, and recommends adjustments -- smaller local
models, cloud fallbacks, or trimmed extension/tooling sets -- so the
restored environment actually runs well on the new machine.

This is implemented as a transparent rule-based analyzer rather than a
live LLM call, so recommendations are deterministic, offline, and fast.
The scoring table can be swapped for a real model-selection API later.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# (min_ram_gb, requires_gpu) -> tier name, sorted largest requirement first
MODEL_TIERS: list[dict[str, Any]] = [
    {"name": "34b-70b class (e.g. llama3:70b-q4, qwen2.5:32b)", "min_ram": 48, "needs_gpu": True},
    {"name": "13b-34b class (e.g. codellama:34b, mixtral:8x7b-q4)", "min_ram": 32, "needs_gpu": True},
    {"name": "7b-13b class (e.g. llama3:8b, codellama:13b-q4)", "min_ram": 16, "needs_gpu": False},
    {"name": "3b-7b quantized class (e.g. phi3:mini, gemma2:2b, qwen2.5-coder:3b)", "min_ram": 8, "needs_gpu": False},
    {"name": "1b-3b tiny class (e.g. qwen2.5:1.5b, tinyllama)", "min_ram": 4, "needs_gpu": False},
]

CLOUD_FALLBACKS = [
    "Anthropic Claude API (claude-sonnet / claude-haiku) via Continue or Cline",
    "GitHub Copilot (cloud-hosted, minimal local footprint)",
    "OpenAI API (gpt-4o-mini for lightweight cloud completion)",
]


@dataclass
class HardwareProfile:
    ram_gb: float
    cpu_cores: int
    has_gpu: bool
    gpu_names: list[str] = field(default_factory=list)

    @classmethod
    def from_system_dict(cls, system: dict[str, Any]) -> "HardwareProfile":
        gpu = system.get("gpu") or []
        return cls(
            ram_gb=float(system.get("ram_gb", 0) or 0),
            cpu_cores=int(system.get("cpu_cores", 0) or 0),
            has_gpu=len(gpu) > 0,
            gpu_names=gpu,
        )


class HardwareOptimizer:
    """Rule-based old-machine vs new-machine model/config advisor."""

    def analyze(self, old: HardwareProfile, new: HardwareProfile,
                configured_ollama_models: list[str] | None = None) -> dict[str, Any]:
        old_tier = self._best_tier(old)
        new_tier = self._best_tier(new)

        downgraded = self._tier_rank(new_tier) > self._tier_rank(old_tier)
        ram_delta = round(new.ram_gb - old.ram_gb, 1)

        recommendations: list[str] = []
        warnings: list[str] = []

        if downgraded:
            warnings.append(
                f"New machine ({new.ram_gb}GB RAM, GPU={'yes' if new.has_gpu else 'no'}) "
                f"is a step down from the old machine ({old.ram_gb}GB RAM, "
                f"GPU={'yes' if old.has_gpu else 'no'})."
            )
            recommendations.append(
                f"Switch local models from the '{old_tier['name']}' tier down to the "
                f"'{new_tier['name']}' tier to fit in available RAM."
            )
            if not new.has_gpu and old.has_gpu:
                recommendations.append(
                    "No GPU detected on the new machine -- expect slower local inference; "
                    "favor quantized (Q4/Q5) GGUF models over full-precision weights."
                )
            recommendations.extend(f"Consider cloud fallback: {c}" for c in CLOUD_FALLBACKS[:2])
        elif ram_delta > 0:
            recommendations.append(
                f"New machine has more headroom (+{ram_delta}GB RAM). You could move up to the "
                f"'{new_tier['name']}' tier for better local model quality."
            )
        else:
            recommendations.append("Hardware is roughly equivalent; existing model choices should carry over as-is.")

        # Per-configured-model guidance
        model_notes: list[str] = []
        if configured_ollama_models:
            for m in configured_ollama_models:
                note = self._per_model_note(m, new)
                if note:
                    model_notes.append(note)

        return {
            "old_tier": old_tier["name"],
            "new_tier": new_tier["name"],
            "downgraded": downgraded,
            "ram_delta_gb": ram_delta,
            "warnings": warnings,
            "recommendations": recommendations,
            "model_specific_notes": model_notes,
            "cloud_alternatives": CLOUD_FALLBACKS,
        }

    def _best_tier(self, hw: HardwareProfile) -> dict[str, Any]:
        for tier in MODEL_TIERS:
            if hw.ram_gb >= tier["min_ram"] and (not tier["needs_gpu"] or hw.has_gpu):
                return tier
        return MODEL_TIERS[-1]

    def _tier_rank(self, tier: dict[str, Any]) -> int:
        return MODEL_TIERS.index(tier)

    def _per_model_note(self, model_name: str, new_hw: HardwareProfile) -> str | None:
        lname = model_name.lower()
        big_hint = any(s in lname for s in ("70b", "34b", "33b", "32b", "mixtral"))
        mid_hint = any(s in lname for s in ("13b", "14b", "8x7b"))

        if big_hint and new_hw.ram_gb < 32:
            return (f"'{model_name}' likely won't fit comfortably in {new_hw.ram_gb}GB RAM -- "
                    f"suggest a quantized 7b-13b alternative (e.g. codellama:13b-q4) or a cloud model.")
        if mid_hint and new_hw.ram_gb < 16:
            return (f"'{model_name}' may be too large for {new_hw.ram_gb}GB RAM -- "
                    f"suggest dropping to a 3b-7b quantized model.")
        return None
