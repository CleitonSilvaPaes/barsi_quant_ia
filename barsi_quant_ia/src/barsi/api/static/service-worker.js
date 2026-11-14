/**
 * Barsi Quant IA - Service Worker PWA
 * Versão do Cache: barsi-pwa-v1.0
 */

const CACHE_NAME = 'barsi-pwa-v1.0';

const PRECACHE_ASSETS = [
  '/',
  '/manifest.json',
  '/static/icons/icon-192.png',
  '/static/icons/icon-512.png',
  '/static/icons/icon-192.svg',
  '/static/icons/icon-512.svg',
  '/static/js/common.js'
];

// Instalação: Pré-cache dos ativos estáticos fundamentais
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      return cache.addAll(PRECACHE_ASSETS).catch((err) => {
        console.warn('PWA Pre-cache parcial:', err);
      });
    }).then(() => self.skipWaiting())
  );
});

// Ativação: Limpeza de versões anteriores do cache
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((cacheNames) => {
      return Promise.all(
        cacheNames.map((name) => {
          if (name !== CACHE_NAME) {
            return caches.delete(name);
          }
        })
      );
    }).then(() => self.clients.claim())
  );
});

// Interceptação de Requisições
self.addEventListener('fetch', (event) => {
  const req = event.request;
  const url = new URL(req.url);

  // Não cachear chamadas da API POST/PUT/DELETE ou websockets
  if (req.method !== 'GET' || url.pathname.startsWith('/api/run') || url.pathname.startsWith('/auth/')) {
    return;
  }

  // 1. Assets estáticos (ícones, JS): Cache First com fallback de rede
  if (url.pathname.startsWith('/static/')) {
    event.respondWith(
      caches.match(req).then((cached) => {
        if (cached) return cached;
        return fetch(req).then((response) => {
          if (response && response.status === 200) {
            const respClone = response.clone();
            caches.open(CACHE_NAME).then((cache) => cache.put(req, respClone));
          }
          return response;
        });
      })
    );
    return;
  }

  // 2. Páginas HTML e navegação: Network First com fallback para Cache
  if (req.headers.get('accept') && req.headers.get('accept').includes('text/html')) {
    event.respondWith(
      fetch(req)
        .then((response) => {
          if (response && response.status === 200) {
            const respClone = response.clone();
            caches.open(CACHE_NAME).then((cache) => cache.put(req, respClone));
          }
          return response;
        })
        .catch(() => caches.match(req).then((cached) => cached || caches.match('/')))
    );
    return;
  }

  // 3. Demais requisições GET: Network First
  event.respondWith(
    fetch(req).catch(() => caches.match(req))
  );
});

