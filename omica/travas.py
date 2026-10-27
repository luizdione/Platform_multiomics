"""travas.py — trava exclusiva entre threads e processos, por arquivo.

Trava de KERNEL (flock no Linux do container, msvcrt.locking no host Windows), não
arquivo-sentinela criado com O_EXCL: se o processo morre segurando a trava, o sistema
operacional a solta sozinho. Com sentinela era preciso adivinhar quando uma trava estava
"velha" e apagá-la — e dois processos apagando ao mesmo tempo apagam um a trava recém-criada
do outro.

O arquivo da trava nunca é apagado (apagar reabre a mesma corrida). Cada `open()` é uma
descrição de arquivo própria, então duas threads do MESMO processo também se excluem.
"""
from __future__ import annotations

import contextlib
import os
import time
from pathlib import Path
from typing import BinaryIO, Iterator

if os.name == "nt":  # host Windows (testes e CLI fora do container)
    import msvcrt

    def _tentar(f: BinaryIO) -> None:
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)

    def _soltar(f: BinaryIO) -> None:
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def _tentar(f: BinaryIO) -> None:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _soltar(f: BinaryIO) -> None:
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)


class TravaOcupada(RuntimeError):
    """Outro dono segurou a trava além do tempo de espera."""


@contextlib.contextmanager
def trava(caminho: Path, espera_s: float = 10.0) -> Iterator[None]:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    f = open(caminho, "a+b")
    limite = time.monotonic() + espera_s
    try:
        while True:
            try:
                _tentar(f)
                break
            except OSError:
                if time.monotonic() >= limite:
                    raise TravaOcupada(str(caminho)) from None
                time.sleep(0.05)
        try:
            yield
        finally:
            _soltar(f)
    finally:
        f.close()
