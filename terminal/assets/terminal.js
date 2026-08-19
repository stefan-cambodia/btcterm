// Échappe du plein écran : on reclique le bouton du panneau agrandi, ce
// qui laisse toute la logique d'état dans le callback clientside.
document.addEventListener("keydown", function (event) {
    if (event.key !== "Escape") {
        return;
    }
    const button = document.querySelector(".cell-max .zoom-btn");
    if (button) {
        button.click();
    }
});
