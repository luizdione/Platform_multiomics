#!/usr/bin/env bash
# snapshot_wip.sh — cópia de segurança do trabalho NÃO commitado da ômica.
#
# Por quê: trabalho longo feito por agentes (workflows) pode ser interrompido no meio — fim
# de cota, queda de energia — e um `git checkout`/`stash` errado apaga o que não foi
# commitado. Esta cópia guarda os arquivos modificados e os novos (os ignorados pelo git,
# como o .env do token, ficam DE FORA) num tar.gz com sha256 e o commit-base ao lado.
# Só grava quando algo mudou desde a última cópia.
#
# Uso (Git Bash, na raiz do repositório):
#   bash tools/snapshot_wip.sh                    # uma cópia agora
#   bash tools/snapshot_wip.sh --laco 15          # a cada 15 min, até ser interrompido
#   bash tools/snapshot_wip.sh --manter 24        # quantas cópias guardar (padrão 24)
# Restaurar: tar --force-local -xzf <arquivo> -C <pasta> (conferir o .base = commit de origem).
set -u
DESTINO="${OMICA_WIP_BACKUP:-/e/data/_backups/omica/wip}"
LACO=0
MANTER=24
while [ $# -gt 0 ]; do
  case "$1" in
    --laco) LACO="$2"; shift 2 ;;
    --manter) MANTER="$2"; shift 2 ;;
    *) echo "argumento desconhecido: $1" >&2; exit 2 ;;
  esac
done
RAIZ="$(git rev-parse --show-toplevel)" || exit 1
cd "$RAIZ" || exit 1
mkdir -p "$DESTINO"

uma_copia() {
  local lista assinatura ultima ts
  lista="$(mktemp)"
  git ls-files -m -o --exclude-standard -z > "$lista"
  # assinatura barata do conteúdo: nome + tamanho + mtime de cada arquivo da lista
  assinatura="$(xargs -0 -r stat -c '%n %s %Y' < "$lista" 2>/dev/null | sha256sum | cut -c1-64)"
  ultima="$(cat "$DESTINO/.ultima_assinatura" 2>/dev/null || true)"
  if [ "$assinatura" = "$ultima" ]; then
    rm -f "$lista"
    return 0
  fi
  ts="$(date +%Y%m%d_%H%M%S)"
  if tar --force-local --null -T "$lista" -czf "$DESTINO/omica_wip_$ts.tar.gz"; then
    git rev-parse HEAD > "$DESTINO/omica_wip_$ts.base"
    (cd "$DESTINO" && sha256sum "omica_wip_$ts.tar.gz" > "omica_wip_$ts.tar.gz.sha256")
    echo "$assinatura" > "$DESTINO/.ultima_assinatura"
    echo "$(date '+%F %T') cópia omica_wip_$ts.tar.gz ($(tr -cd '\0' < "$lista" | wc -c) arquivos)"
  else
    echo "$(date '+%F %T') FALHA ao gravar a cópia" >&2
  fi
  rm -f "$lista"
  # guarda só as MANTER mais novas
  ls -1t "$DESTINO"/omica_wip_*.tar.gz 2>/dev/null | tail -n +"$((MANTER + 1))" | while read -r velho; do
    rm -f "$velho" "$velho.sha256" "${velho%.tar.gz}.base"
  done
}

uma_copia
if [ "$LACO" -gt 0 ]; then
  while sleep "$((LACO * 60))"; do uma_copia; done
fi
