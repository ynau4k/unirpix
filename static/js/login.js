// Interações pequenas e acessíveis do formulário de login.
document.addEventListener("DOMContentLoaded", () => {
  const cpfInput = document.querySelector("#cpf");
  const senhaInput = document.querySelector("#senha");
  const togglePassword = document.querySelector(".toggle-password");
  const loginForm = document.querySelector("#form-login");

  // Aplica a máscara enquanto a pessoa digita, sem alterar o valor guardado.
  cpfInput?.addEventListener("input", () => {
    const numeros = cpfInput.value.replace(/\D/g, "").slice(0, 11);
    cpfInput.value = numeros
      .replace(/(\d{3})(\d)/, "$1.$2")
      .replace(/(\d{3})(\d)/, "$1.$2")
      .replace(/(\d{3})(\d{1,2})$/, "$1-$2");
    cpfInput.setCustomValidity("");
  });

  // Alterna a visibilidade da senha sem enviar ou armazenar o conteúdo.
  togglePassword?.addEventListener("click", () => {
    const mostrar = senhaInput.type === "password";
    senhaInput.type = mostrar ? "text" : "password";
    togglePassword.setAttribute("aria-pressed", String(mostrar));
    togglePassword.setAttribute("aria-label", mostrar ? "Ocultar senha" : "Mostrar senha");
  });

  // Dá uma indicação imediata se os campos obrigatórios estiverem vazios.
  loginForm?.addEventListener("submit", (event) => {
    const cpfNumeros = cpfInput.value.replace(/\D/g, "");

    if (cpfNumeros.length !== 11) {
      event.preventDefault();
      cpfInput.setCustomValidity("Digite os 11 números do CPF.");
      cpfInput.reportValidity();
      cpfInput.focus();
      return;
    }

    if (!senhaInput.value.trim()) {
      event.preventDefault();
      senhaInput.setCustomValidity("Informe sua senha.");
      senhaInput.reportValidity();
      senhaInput.focus();
      return;
    }

    senhaInput.setCustomValidity("");
  });
});
