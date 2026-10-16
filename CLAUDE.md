# Priorité
Je préfère un code imparfait que je comprends à un code parfait que je ne comprends pas.

# Style de code
- Code simple et linéaire : il se lit de haut en bas (données, puis fonctions, puis exécution).
- État en mémoire (dicts, listes). Pas de base de données ni de fichier de persistance sauf si je le demande.
- Des if explicites. Pas de ternaires imbriqués ni de one-liners denses.
- Pas de code défensif : pas de try/except, de validations ou de cas limites que je n'ai pas demandés.
- Pas d'abstraction prématurée : pas de classes, de couches ou de helpers génériques si une fonction simple suffit.
- Noms de variables clairs, écrits en entier.
- Peu de commentaires, seulement quand le "pourquoi" n'est pas évident.

# Façon de travailler
- Avant de coder, présente un plan court (5 lignes max) et attends ma validation.
- Une petite modification à la fois. Arrête-toi après chaque étape pour que je relise.
- Ne touche qu'aux fichiers nécessaires à la tâche. Pas de refacto non demandée.
- Pas de nouvelle dépendance sans me demander.
- Après chaque modification, résume en 3 lignes ce qui a changé et pourquoi.