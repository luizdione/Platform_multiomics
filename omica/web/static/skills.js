// skills.js — busca offline nas 815 skills (JSON embutido na página; zero rede,
// zero LLM). Filtro por texto (id+descrição+stack) e por domínio.
(function () {
  const dados = JSON.parse(document.getElementById("dados-skills").textContent);
  const linhas = document.getElementById("linhas");
  const busca = document.getElementById("busca");
  const dominio = document.getElementById("dominio");
  const contagem = document.getElementById("contagem");
  const LIMITE = 200;

  function render() {
    const q = busca.value.trim().toLowerCase();
    const d = dominio.value;
    const achadas = dados.skills.filter(s =>
      (!d || s.dominio === d) &&
      (!q || s.id.includes(q) || s.descricao.toLowerCase().includes(q) ||
        s.stack.join(",").toLowerCase().includes(q))
    );
    contagem.textContent = ` — ${achadas.length} skill(s)` +
      (achadas.length > LIMITE ? ` (mostrando ${LIMITE})` : "");
    linhas.innerHTML = achadas.slice(0, LIMITE).map(s =>
      `<tr><td><a href="/skills/${encodeURIComponent(s.pasta)}">${s.id}</a></td>` +
      `<td><code>${s.dominio}</code></td><td>${s.descricao}</td>` +
      `<td>${s.stack.slice(0, 5).map(x => `<code>${x}</code>`).join(" ")}</td></tr>`
    ).join("");
  }
  busca.addEventListener("input", render);
  dominio.addEventListener("change", render);
  render();
})();
