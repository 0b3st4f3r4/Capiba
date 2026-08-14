# language: pt
Funcionalidade: Portal Capiba (dashboard)
  O portal é a página de entrada do cluster: lista os serviços
  disponíveis, exibe estatísticas do lake e exige login via
  Keycloak quando o SSO está habilitado.

  Cenário: Portal acessível sem SSO
    Dado que o SSO está desabilitado
    Quando o usuário acessa a página inicial do portal
    Então a página lista os serviços do cluster

  Cenário: SSO habilitado exige login
    Dado que o SSO está habilitado
    Quando o usuário acessa a página inicial do portal
    Então o usuário é redirecionado para o login

  Cenário: Estatísticas indisponíveis não quebram a página
    Dado que o SSO está desabilitado
    E as fontes de estatísticas estão indisponíveis
    Quando o usuário acessa a página inicial do portal
    Então a página é exibida com estatísticas indisponíveis
