// omica.js — console de job ao vivo via SSE (EventSource nativo, zero dependência
// externa: funciona offline em sala de aula). Recarrega a página ao fim do job
// para exibir o resultado lido do arquivo.
(function () {
  const consoleEl = document.getElementById("console");
  if (!consoleEl) return;
  const jobId = consoleEl.dataset.job;
  const encerrado = consoleEl.dataset.encerrado === "1";

  const fonte = new EventSource(`/jobs/${jobId}/events`);
  fonte.onmessage = (ev) => {
    consoleEl.textContent += ev.data + "\n";
    consoleEl.scrollTop = consoleEl.scrollHeight;
  };
  fonte.addEventListener("fim", () => {
    fonte.close();
    if (!encerrado) setTimeout(() => window.location.reload(), 600);
  });
  fonte.onerror = () => fonte.close();
})();
