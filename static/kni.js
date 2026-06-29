const CACHE_NAME = "trainer-cache-v1";
const FILES_TO_CACHE = [
  "./index.html",
  "./logo.png",
  "./kni.json"
];
self.addEventListener("install", (event) => {
  console.log("Service Worker Installed ");

  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      console.log("Caching app files...");
      return cache.addAll(FILES_TO_CACHE);
    })
  );
});
self.addEventListener("activate", (event) => {
  console.log("Service Worker Activated");
  event.waitUntil(
    caches.keys().then((cacheNames) =>
      Promise.all(
        cacheNames.map((cache) => {
          if (cache !== CACHE_NAME) {
            console.log("Deleting old cache:", cache);
            return caches.delete(cache);
          }
        })
      )
    )
  );
});
self.addEventListener("fetch", (event) => {
  event.respondWith(
    caches.match(event.request).then((response) => {
      return response || fetch(event.request);
    })
  );
});