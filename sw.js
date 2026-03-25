const CACHE_NAME = "site-cache-v1";

self.addEventListener("install", event => {

event.waitUntil(

caches.open(CACHE_NAME).then(cache => {

return cache.addAll([
"/",
"/index.html"
]);

})

);

self.skipWaiting();

});

self.addEventListener("activate", event => {

event.waitUntil(

caches.keys().then(cacheNames => {

return Promise.all(

cacheNames.map(cache => {

if (cache !== CACHE_NAME) {
return caches.delete(cache);
}

})

);

})

);

self.clients.claim();

});

self.addEventListener("fetch", event => {

event.respondWith(

caches.match(event.request).then(response => {

if (response) {
return response;
}

return fetch(event.request).then(networkResponse => {

return caches.open(CACHE_NAME).then(cache => {

cache.put(event.request, networkResponse.clone());
return networkResponse;

});

});

})

);

});
