"""Optional pip install 'g6[ml]' to run on an installed G6 checkout."""
from sklearn.ensemble import RandomForestClassifier
from sklearn.tree import DecisionTreeClassifier
from g6.cognitive_bridge import extract_tree, distill_blackbox

X_train = [[i / 20.0, i % 7] for i in range(80)]
y = [int(row[0] > 2) for row in X_train]
X_holdout = [[i / 20.0, i % 7] for i in range(80, 110)]

tree = DecisionTreeClassifier(max_depth=3, random_state=7).fit(X_train, y)
print("Tree directly transcribed:", extract_tree(tree, ["x", "z"]).explain_one([2.5, 1]))

blackbox = RandomForestClassifier(n_estimators=25, random_state=7).fit(X_train, y)
explicit, report = distill_blackbox(blackbox, X_train, X_holdout, ["x", "z"], max_depth=3)
print("Black-box surrogate fidelity:", report)
print("Human-inspectable surrogate:", explicit.explain_one([2.5, 1]))
