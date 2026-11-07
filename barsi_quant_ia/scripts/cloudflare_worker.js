/**
 * Cloudflare Worker com Cron Trigger para Orquestração Externa Barsi.
 *
 * Configuração no wrangler.toml:
 * name = "barsi-cron-trigger"
 * main = "src/index.js"
 * compatibility_date = "2024-01-01"
 *
 * [triggers]
 * crons = ["0 9-18 * * 1-5"] # De hora em hora, das 09h às 18h em dias úteis
 */

export default {
  // Disparo agendado via Cloudflare Cron Triggers
  async scheduled(event, env, ctx) {
    const TUNNEL_URL = env.BARSI_TUNNEL_URL; // Ex: https://barsi.seudominio.com/run
    const BEARER_TOKEN = env.BARSI_API_BEARER_TOKEN;

    console.log(`[Cron Trigger] Disparando execução Barsi em ${TUNNEL_URL}`);

    const response = await fetch(TUNNEL_URL, {
      method: "POST",
      headers: {
        "Authorization": `Bearer ${BEARER_TOKEN}`,
        "Content-Type": "application/json"
      }
    });

    const result = await response.json();
    console.log("[Cron Trigger] Resposta recebida:", JSON.stringify(result));
  },

  // Suporte a chamada manual via HTTP
  async fetch(request, env, ctx) {
    return new Response("Barsi Cron Worker Online", { status: 200 });
  }
};

