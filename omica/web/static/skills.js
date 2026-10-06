// skills.js — a busca roda no SERVIDOR (GET /api/skills: sem acento, português→inglês,
// todos os termos, modo parcial para frase inteira). Aqui só se desenha o que voltou.
// Zero rede externa, zero LLM.
(function () {
  const T = JSON.parse(document.getElementById("textos-skills").textContent);
  const linhas = document.getElementById("linhas");
  const busca = document.getElementById("busca");
  const dominio = document.getElementById("dominio");
  const contagem = document.getElementById("contagem");
  const relato = document.getElementById("relato");
  const cards = document.getElementById("cards");
  let pedido = 0;
  let espera = null;

  // descrição e card vêm da biblioteca (texto de terceiros): nunca direto no innerHTML
  function esc(s) {
    return String(s).replace(/[&<>"']/g, c =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  function enderecar() {
    const p = new URLSearchParams();
    if (busca.value.trim()) p.set("q", busca.value.trim());
    if (dominio.value) p.set("dominio", dominio.value);
    const qs = p.toString();
    history.replaceState(null, "", qs ? "/skills?" + qs : "/skills");
  }

  function desenhar(r) {
    contagem.textContent = ` — ${r.total} ${T.skills}` +
      (r.total > r.mostrando ? ` (${T.mostrando} ${r.mostrando})` : "");
    const partes = [];
    if (r.traducao.length) {
      partes.push(T.traducao + ": " +
        r.traducao.map(x => `${esc(x.termo)} → ${esc(x.para.join(" / "))}`).join("; "));
    }
    if (r.ignorados.length) partes.push(T.ignorado + ": " + esc(r.ignorados.join(", ")));
    if (r.sem_casar.length) partes.push(T.sem_casar + ": " + esc(r.sem_casar.join(", ")));
    if (r.truncado) partes.push(T.truncado);
    if (r.modo === "parcial") partes.unshift("⚠️ " + T.parcial);
    relato.innerHTML = partes.join(" · ");

    // Guias do domínio: abertos ao folhear o domínio, recolhidos durante a busca — senão
    // três cards longos empurram a tabela de resultados para baixo da dobra.
    cards.innerHTML = !r.cards.length ? "" :
      `<details${busca.value.trim() ? "" : " open"}><summary>📇 ${r.cards.length} ${T.guias}: ` +
      r.cards.map(c => esc(c.titulo)).join(" · ") + `</summary><div class="cards">` +
      r.cards.map(c =>
        `<div class="card"><h3 style="overflow-wrap:anywhere">${esc(c.titulo)}</h3>` +
        `<p><strong>${T.quando}:</strong> ${esc(c.quando)}</p>` +
        (c.regras ? `<p><strong>${T.regras}:</strong> ${esc(c.regras)}</p>` : "") +
        (c.stack ? `<p class="meta">Stack: ${esc(c.stack)}</p>` : "") + `</div>`
      ).join("") + `</div></details>`;

    linhas.innerHTML = r.resultados.map(s => {
      const destino = "/skills/" + s.pasta.split("/").map(encodeURIComponent).join("/");
      const casou = s.casou ? `<br><span class="meta">${T.casou}: ${esc(s.casou.join(", "))}</span>` : "";
      return `<tr><td><a href="${destino}">${esc(s.id)}</a></td>` +
        `<td><code>${esc(s.dominio)}</code></td><td>${esc(s.descricao)}${casou}</td>` +
        `<td>${s.stack.slice(0, 5).map(x => `<code>${esc(x)}</code>`).join(" ")}</td></tr>`;
    }).join("");
  }

  async function atualizar() {
    const meu = ++pedido;
    enderecar();
    const url = "/api/skills?q=" + encodeURIComponent(busca.value) +
      "&dominio=" + encodeURIComponent(dominio.value);
    let r;
    try {
      r = await (await fetch(url)).json();
    } catch (e) {
      if (meu === pedido) contagem.textContent = " — " + T.erro;
      return;
    }
    if (meu === pedido) desenhar(r);  // resposta velha (o usuário já digitou mais) é descartada
  }

  busca.addEventListener("input", function () {
    clearTimeout(espera);
    espera = setTimeout(atualizar, 150);
  });
  dominio.addEventListener("change", atualizar);
  atualizar();
})();
