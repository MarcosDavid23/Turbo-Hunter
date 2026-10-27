TURBO HUNTER 0.4.4
Kill Tracker & Lost Harvest Finder / Rastreador de Abates e Localizador de Colheita
for theHunter: Call of the Wild
Author / Autor: DavidCPU
Supported game build / Build do jogo suportada: 3304878
======================================================================

ENGLISH
======================================================================

ABOUT TURBO HUNTER
Turbo Hunter helps you keep track of animals you have killed and points
the in-game GPS toward the nearest uncollected animal.

The HUD shows how many pending kills are still waiting to be recovered.

----------------------------------------------------------------------
INSTALLATION
----------------------------------------------------------------------

1. Extract the complete ZIP file to a folder.
2. Run "INSTALAR TURBO HUNTER.cmd".
3. Click INSTALL.
4. Keep the visible log console open while Turbo Hunter prepares Python
   and Frida automatically.
5. When installation is complete, use "INICIAR TURBO HUNTER.vbs".

IMPORTANT:
- Internet access may be required during the first installation.
- If a compatible Python 3 installation already exists, Turbo Hunter
  uses it.
- If Python is missing, the verified official version is downloaded and
  prepared automatically, just like version 0.4.3.
- The installation steps and pip output remain visible in the log console.

----------------------------------------------------------------------
HOW TO USE
----------------------------------------------------------------------

Recommended order:

1. Open "INICIAR TURBO HUNTER.vbs".
2. Leave Turbo Hunter on WAITING FOR GAME.
3. Open theHunter: Call of the Wild.
4. Turbo Hunter will connect automatically when the game is detected.
5. Enter a SOLO hunt.
6. Wait until the HUD is active and shows KILLS: 0.
7. Hunt normally.
8. When there is a pending kill, the in-game GPS will point toward the
   nearest uncollected animal.

Keep Turbo Hunter running while you play.

----------------------------------------------------------------------
KEYS
----------------------------------------------------------------------

F8 = Move the Turbo Hunter HUD between the four corners of the screen.
F9 = Show or hide the Turbo Hunter HUD.

----------------------------------------------------------------------
SETTINGS
----------------------------------------------------------------------

SOLO protection

1 = SOLO only.
    Multiplayer is blocked.

0 = Multiplayer is allowed at your own risk.
    This option exists for users who intentionally want to play with
    family or friends. SOLO protection remains the recommended setting.


Waypoint protection

1 = Protects your manual waypoint.
    Turbo Hunter waits until you clear your waypoint before using the
    animal GPS again.

0 = No waypoint protection.
    Turbo Hunter may automatically move or reclaim the waypoint.

----------------------------------------------------------------------
IMPORTANT NOTES
----------------------------------------------------------------------

- Turbo Hunter is designed to work while it is running and connected
  to the game.
- For the best results, start Turbo Hunter before starting your hunt.
- Keep the complete Turbo Hunter folder together. Do not move individual
  files out of the extracted folder.
- If Turbo Hunter is waiting for the game, simply open theHunter:
  Call of the Wild and let detection happen automatically.
- Turbo Hunter 0.4.4 checks the game build and native address ranges.
  If they are incompatible, GPS and HUD are blocked before kills are
  processed. This prevents unsafe hooks after a game update.


======================================================================
PORTUGUES (BRASIL) - PT-BR
======================================================================

SOBRE O TURBO HUNTER
O Turbo Hunter ajuda a acompanhar os animais abatidos e aponta o GPS
do jogo para o animal não coletado mais próximo.

O HUD mostra quantos abates pendentes ainda precisam ser recuperados.

----------------------------------------------------------------------
INSTALACAO
----------------------------------------------------------------------

1. Extraia todo o conteúdo do ZIP para uma pasta.
2. Execute "INSTALAR TURBO HUNTER.cmd".
3. Clique em INSTALAR.
4. Mantenha o console de log visível aberto enquanto o Turbo Hunter
   prepara automaticamente o Python e o Frida.
5. Quando a instalação terminar, use "INICIAR TURBO HUNTER.vbs".

IMPORTANTE:
- Pode ser necessário acesso à internet na primeira instalação.
- Se já existir um Python 3 compatível, o Turbo Hunter utiliza ele.
- Se o Python estiver ausente, a versão oficial verificada é baixada e
  preparada automaticamente, igual acontecia na versão 0.4.3.
- As etapas da instalação e a saída do pip ficam visíveis no console.

----------------------------------------------------------------------
COMO USAR
----------------------------------------------------------------------

Ordem recomendada:

1. Abra "INICIAR TURBO HUNTER.vbs".
2. Deixe o Turbo Hunter em AGUARDANDO JOGO.
3. Abra o theHunter: Call of the Wild.
4. O Turbo Hunter conectara automaticamente quando detectar o jogo.
5. Entre em uma caçada SOLO.
6. Aguarde o HUD ficar ativo e mostrar ABATES: 0.
7. Cace normalmente.
8. Quando houver um abate pendente, o GPS do jogo apontara para o
   animal não coletado mais próximo.

Mantenha o Turbo Hunter aberto enquanto estiver jogando.

----------------------------------------------------------------------
TECLAS
----------------------------------------------------------------------

F8 = Move o HUD do Turbo Hunter entre os quatro cantos da tela.
F9 = Mostra ou oculta o HUD do Turbo Hunter.

----------------------------------------------------------------------
CONFIGURACOES
----------------------------------------------------------------------

Proteção SOLO

1 = Somente SOLO.
    O multiplayer é bloqueado.

0 = Permite multiplayer por conta e risco do usuário.
    Essa opção existe para quem deseja jogar intencionalmente com
    familiares ou amigos. A proteção SOLO continua sendo recomendada.


Proteção de waypoint

1 = Protege o seu waypoint manual.
    O Turbo Hunter espera você limpar o waypoint antes de voltar a usar
    o GPS dos animais.

0 = Sem proteção de waypoint.
    O Turbo Hunter pode mover ou reassumir o waypoint automaticamente.

----------------------------------------------------------------------
OBSERVACOES IMPORTANTES
----------------------------------------------------------------------

- O Turbo Hunter foi feito para funcionar enquanto estiver aberto e
  conectado ao jogo.
- Para melhores resultados, inicie o Turbo Hunter antes de começar
  a caçada.
- Mantenha todos os arquivos do Turbo Hunter juntos na mesma pasta.
  Não mova arquivos individuais para fora da pasta extraída.
- Se o Turbo Hunter estiver em AGUARDANDO JOGO, basta abrir o
  theHunter: Call of the Wild e aguardar a detecçao automática.
- O Turbo Hunter 0.4.4 confere a build do jogo e as faixas dos endereços
  nativos. Se forem incompatíveis, GPS e HUD são bloqueados antes de
  processar abates. Isso evita hooks inseguros depois de uma atualização.

======================================================================
WHAT CHANGED IN 0.4.4 / O QUE MUDOU NA 0.4.4
======================================================================

- The complete automatic installation and game behavior from 0.4.3
  were preserved.
- Installation now starts through a visible CMD window.
- The worker console shows installation steps and pip output live.

- Toda a instalação automática e o funcionamento da versão 0.4.3
  foram mantidos.
- A instalação agora começa por uma janela CMD visível.
- O console mostra ao vivo as etapas da instalação e a saída do pip.


======================================================================
Turbo Hunter 0.4.4
Happy hunting! / Boa caçada!
======================================================================
