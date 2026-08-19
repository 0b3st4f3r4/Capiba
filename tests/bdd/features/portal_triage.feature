# language: pt
Funcionalidade: Página de triagem editorial no portal
  A página /triage é a interface humana da fila editorial (O10): lista os
  sinais por estado, aplica transições com revisor identificado e exige
  motivo no descarte — nenhum sinal sai como confirmado sem revisor.

  Cenário: Fila lista os sinais pendentes
    Dado que o SSO está desabilitado
    E um sinal "single_bid" do fornecedor "12345678000199" aguardando revisão
    Quando o usuário acessa a página de triagem
    Então a página lista o sinal "single_bid" de "12345678000199"

  Cenário: Revisor confirma um sinal pela página
    Dado que o SSO está desabilitado
    E um sinal "single_bid" do fornecedor "12345678000199" aguardando revisão
    Quando a revisora "ana" confirma o sinal pela página
    Então o sinal "single_bid" de "12345678000199" fica "confirmed" por "ana"

  Cenário: Descarte sem motivo mostra erro e mantém o sinal pendente
    Dado que o SSO está desabilitado
    E um sinal "single_bid" do fornecedor "12345678000199" aguardando revisão
    Quando a revisora "ana" rejeita o sinal sem motivo pela página
    Então a página de triagem mostra um aviso de erro
    E o sinal "single_bid" de "12345678000199" segue "pending_review"

  Cenário: SSO habilitado exige login na triagem
    Dado que o SSO está habilitado
    Quando o usuário acessa a página de triagem
    Então o usuário é redirecionado para o login
