/* Cliente de eventos por consultas cortas (reemplaza a EventSource).
   Cada EventSource dejaba una conexión abierta para siempre y el navegador de OBS solo permite
   ~6 conexiones simultáneas al mismo servidor: con 6 fuentes abiertas, todo lo demás
   (latidos, consultas) se quedaba esperando y las fuentes aparecían como «desconectadas».
   Aquí cada consulta dura milisegundos y la conexión se libera enseguida. */
/* Páginas dentro de pestañas ocultas del programa miden 0 de ancho: no tiene sentido consultar.
   (En OBS la página siempre tiene ancho, esté o no visible la fuente.) */
function enPantalla(){return document.documentElement.clientWidth>0}
function visible(){return !document.hidden&&enPantalla()}
class Flujo{
  constructor(nombre,ms){this.n=nombre||"";this.ms=ms||1000;this.h={};this.seq=null;this.boot=null;this.ok=false;this.pausado=false;this.onopen=null;this.tick()}
  addEventListener(t,f){(this.h[t]=this.h[t]||[]).push(f)}
  emitir(t,d){(this.h[t]||[]).forEach(f=>{try{f({data:JSON.stringify(d)})}catch(e){console.error(e)}})}
  async tick(){
    if(!enPantalla()){this.pausado=true;setTimeout(()=>this.tick(),2000);return}
    if(this.pausado){this.pausado=false;this.ok=false;this.seq=null}  // al volver, se resincroniza
    let espera=3000;
    try{
      const t=Object.keys(this.h).join(",");
      const q=(this.seq===null?"":"desde="+this.seq+"&")+"n="+encodeURIComponent(this.n)+(t?"&t="+encodeURIComponent(t):"");
      const d=await (await fetch("/api/ev?"+q,{cache:"no-store"})).json();
      if(!this.ok){this.ok=true;if(this.onopen)try{this.onopen()}catch(e){console.error(e)}}
      if(d.boot!==this.boot){this.boot=d.boot;this.emitir("boot",{id:d.boot})}
      this.seq=d.seq; d.ev.forEach(([,t,x])=>this.emitir(t,x)); espera=this.ms;
    }catch{this.ok=false;this.seq=null}
    setTimeout(()=>this.tick(),espera);
  }
}
