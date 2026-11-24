
const canvas = document.getElementById("ruleta");
const ctx = canvas.getContext("2d");
const textoGanador = document.getElementById("reto-ganador");
const marcoRuleta = document.querySelector(".wheel-frame");
const panelGanador = document.querySelector(".winner-panel");
const contenedorParticulas = document.getElementById("particles");
const destello = document.getElementById("flash");

let temporizadorParticulas = null;

const colores = [
    "#f04437", "#ff9b27", "#e8c83f", "#1eb7a6",
    "#493ee6", "#d83b9c", "#287fe0", "#7a45dc",
    "#ed653b", "#20a7bd", "#cfb13a", "#e84e62"
];

let retos = [];
let ultimoSpinId = 0;
let rotacion = 0;
let girando = false;
let ultimoReto = null;

function dibujarRuleta(lista) {
    const w = canvas.width;
    const centro = w / 2;
    const radio = w / 2 - 12;

    ctx.clearRect(0, 0, w, w);

    if (!lista.length) {
        ctx.fillStyle = "#17213c";
        ctx.beginPath();
        ctx.arc(centro, centro, radio, 0, Math.PI * 2);
        ctx.fill();
        ctx.fillStyle = "#ffffff";
        ctx.font = "bold 30px sans-serif";
        ctx.textAlign = "center";
        ctx.fillText("Añade retos", centro, centro);
        return;
    }

    const paso = (Math.PI * 2) / lista.length;
    const inicio = -Math.PI / 2;

    lista.forEach((reto, i) => {
        const a1 = inicio + i * paso;
        const a2 = a1 + paso;

        ctx.beginPath();
        ctx.moveTo(centro, centro);
        ctx.arc(centro, centro, radio, a1, a2);
        ctx.closePath();
        ctx.fillStyle = colores[i % colores.length];
        ctx.fill();

        ctx.strokeStyle = "#fff1a0";
        ctx.lineWidth = 3;
        ctx.stroke();

        // Texto orientado hacia el exterior de cada segmento.
        ctx.save();
        ctx.translate(centro, centro);
        ctx.rotate(a1 + paso / 2);

        const distancia = radio * 0.59;
        const maxAncho = radio * 0.55;
        const palabras = reto.split(/\s+/);
        const lineas = [];
        let linea = "";

        ctx.font = `bold ${Math.max(13, Math.min(23, 250 / lista.length))}px Arial`;

        for (const palabra of palabras) {
            const prueba = linea ? `${linea} ${palabra}` : palabra;

            if (ctx.measureText(prueba).width > maxAncho && linea) {
                lineas.push(linea);
                linea = palabra;
            } else {
                linea = prueba;
            }
        }

        if (linea) lineas.push(linea);

        const altoLinea = 25;
        const maxLineas = 4;
        const visibles = lineas.slice(0, maxLineas);

        if (lineas.length > maxLineas) {
            visibles[maxLineas - 1] = visibles[maxLineas - 1].replace(/[.,;:]?$/, "…");
        }

        ctx.fillStyle = "#ffffff";
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        ctx.shadowColor = "#000000";
        ctx.shadowBlur = 5;

        visibles.forEach((texto, j) => {
            const y = (j - (visibles.length - 1) / 2) * altoLinea;
            ctx.fillText(texto, distancia, y, maxAncho);
        });

        ctx.restore();
    });

    ctx.beginPath();
    ctx.arc(centro, centro, radio, 0, Math.PI * 2);
    ctx.strokeStyle = "#ffe99b";
    ctx.lineWidth = 9;
    ctx.stroke();
}

function animarHasta(indice, idGiro) {
    if (!retos.length || indice < 0 || indice >= retos.length) return;

    girando = true;
iniciarEnergia();
    const paso = 360 / retos.length;
    // El indicador está arriba: el centro del segmento ganador
    // debe quedar a cero grados visuales.
    const objetivo = 360 - (indice + 0.5) * paso;
    const actual = ((rotacion % 360) + 360) % 360;
    const diferencia = ((objetivo - actual) % 360 + 360) % 360;

    const desde = rotacion;
    const hasta = rotacion + 360 * 6 + diferencia;
    const duracion = 5000;
    const inicio = performance.now();

    function frame(ahora) {
        const progreso = Math.min((ahora - inicio) / duracion, 1);
        const suavizado = 1 - Math.pow(1 - progreso, 4);
        rotacion = desde + (hasta - desde) * suavizado;
        canvas.style.transform = `rotate(${rotacion}deg)`;

        if (progreso < 1) {
            requestAnimationFrame(frame);
        } else {
rotacion = hasta;
girando = false;
textoGanador.textContent = ultimoReto || "¡Reto seleccionado!";
terminarEnergia();
        }
    }

    requestAnimationFrame(frame);
}


function crearParticula() {
    if (!contenedorParticulas) return;

    const particula = document.createElement("span");
    particula.className = "energy-particle";

    particula.style.left = `${15 + Math.random() * 70}%`;
    particula.style.top = `${42 + Math.random() * 40}%`;
    particula.style.setProperty(
        "--duration",
        `${1 + Math.random() * 1.5}s`
    );
    particula.style.setProperty(
        "--drift",
        `${Math.round(Math.random() * 100 - 50)}px`
    );

    contenedorParticulas.appendChild(particula);

    particula.addEventListener("animationend", () => {
        particula.remove();
    }, { once: true });
}

function iniciarEnergia() {
    marcoRuleta?.classList.add("spinning");

    if (!temporizadorParticulas) {
        temporizadorParticulas = setInterval(crearParticula, 100);
    }
}

function terminarEnergia() {
    marcoRuleta?.classList.remove("spinning");

    if (temporizadorParticulas) {
        clearInterval(temporizadorParticulas);
        temporizadorParticulas = null;
    }

    marcoRuleta?.classList.remove("impact");
    destello?.classList.remove("active");
    panelGanador?.classList.remove("winner-pop");

    // Reiniciar las animaciones para que se reproduzcan de nuevo.
    void marcoRuleta?.offsetWidth;
    void destello?.offsetWidth;
    void panelGanador?.offsetWidth;

    marcoRuleta?.classList.add("impact");
    destello?.classList.add("active");
    panelGanador?.classList.add("winner-pop");

    for (let i = 0; i < 24; i++) {
        setTimeout(crearParticula, i * 22);
    }
}


async function consultarEstado() {
    try {
        const response = await fetch("/api/retos");
        const data = await response.json();

        const cambioLista =
            JSON.stringify(retos) !== JSON.stringify(data.retos);

        if (cambioLista) {
            retos = data.retos;
            dibujarRuleta(retos);
        }

        if (data.spin_id > ultimoSpinId && !girando) {
            ultimoSpinId = data.spin_id;
            ultimoReto = data.reto_actual;
            textoGanador.textContent = "¡La energía se está acumulando!";

            animarHasta(data.indice_ganador, data.spin_id);
        } else if (!data.reto_actual && !girando) {
            textoGanador.textContent = "Esperando el próximo giro...";
        }
    } catch (error) {
        console.error("Error al consultar la ruleta:", error);
    }
}

dibujarRuleta(retos);
consultarEstado();
setInterval(consultarEstado, 500);


window.addEventListener("load", () => {
    const escena = document.querySelector(".roulette-scene");
    const ruleta = document.querySelector(".wheel-frame");

    if (!escena || !ruleta) {
        console.error("No se encontró la escena o la ruleta.");
        return;
    }

    // Reinicia y ejecuta la animación al cargar la página.
    escena.classList.remove("entering");
    void ruleta.offsetWidth;
    escena.classList.add("entering");

    console.log("Animación de entrada activada.");
});
