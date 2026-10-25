"""In-memory vector index: one matrix per compartment, brute-force cosine (ARCHITECTURE D-02, baseline capacity table).

At personal scale (tens of thousands of vectors) a normalized matrix product takes milliseconds and needs no database
extension, which Apple's system SQLite does not allow. Partitioning by compartment means out-of-scope memory is never scored.
"""

from __future__ import annotations

import numpy as np


class VectorIndex:
    def __init__(self) -> None:
        self._ids: dict[str, list[tuple[str, str]]] = {}
        self._mat: dict[str, np.ndarray] = {}

    def load(self, compartment: str, ids: list[tuple[str, str]], matrix: np.ndarray) -> None:
        self._ids[compartment], self._mat[compartment] = list(ids), matrix.astype(np.float32, copy=False)

    def add(self, compartment: str, owner: tuple[str, str], vec: np.ndarray) -> None:
        v = np.asarray(vec, dtype=np.float32).reshape(1, -1)
        if compartment not in self._mat or self._mat[compartment].size == 0:
            self._ids[compartment], self._mat[compartment] = [owner], v
            return
        self._ids[compartment].append(owner)
        self._mat[compartment] = np.vstack([self._mat[compartment], v])

    def remove(self, compartment: str, owners: set[tuple[str, str]]) -> None:
        if compartment not in self._ids:
            return
        keep = [i for i, o in enumerate(self._ids[compartment]) if o not in owners]
        self._ids[compartment] = [self._ids[compartment][i] for i in keep]
        self._mat[compartment] = self._mat[compartment][keep] if keep else np.zeros((0, 0), dtype=np.float32)

    def size(self, compartment: str) -> int:
        return len(self._ids.get(compartment, []))

    def search(self, compartment: str, qvec: np.ndarray, k: int = 20, owner_types: tuple[str, ...] | None = None) -> list[tuple[tuple[str, str], float]]:
        mat = self._mat.get(compartment)
        if mat is None or mat.size == 0:
            return []
        scores = mat @ np.asarray(qvec, dtype=np.float32)
        if owner_types is not None:
            mask = np.fromiter((o[0] in owner_types for o in self._ids[compartment]), dtype=bool, count=len(self._ids[compartment]))
            scores = np.where(mask, scores, -np.inf)
        k = min(k, scores.shape[0])
        top = np.argpartition(-scores, k - 1)[:k] if k < scores.shape[0] else np.arange(scores.shape[0])
        top = top[np.argsort(-scores[top])]
        return [(self._ids[compartment][i], float(scores[i])) for i in top if np.isfinite(scores[i])]
