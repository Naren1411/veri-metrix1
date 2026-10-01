(function(){
  window.__vmxPwaState={installed:false,online:navigator.onLine,secureQueue:"AES-GCM / IndexedDB"};
  if("serviceWorker" in navigator){
    navigator.serviceWorker.register("/sw.js",{scope:"/"})
      .then(function(){window.__vmxPwaState.installed=true;document.dispatchEvent(new CustomEvent("vmx-pwa-ready"))})
      .catch(function(){});
  }
  window.addEventListener("online",function(){window.__vmxPwaState.online=true;document.dispatchEvent(new CustomEvent("vmx-network-change"))});
  window.addEventListener("offline",function(){window.__vmxPwaState.online=false;document.dispatchEvent(new CustomEvent("vmx-network-change"))});
})();