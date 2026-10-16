import numpy as np

X = np.array([1, 2, 3, 4, 5, 6, 7, 8])
y = np.array([35, 40, 50, 55, 65, 70, 78, 85])

w = 0
b = 0

learning_rate = 0.01
epochs = 1000

for _epoch in range(epochs):

    # Prediction
    y_pred = w * X + b

    # Loss
    loss = np.mean((y - y_pred) ** 2)

    # Gradients
    dw = (-2 / len(X)) * np.sum(X * (y - y_pred))
    db = (-2 / len(X)) * np.sum(y - y_pred)

    # Update parameters
    w = w - learning_rate * dw
    b = b - learning_rate * db

print("w:", w)
print("b:", b)
print("loss:", loss)
