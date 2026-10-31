import time

# Output streams live into the console. Try pressing Stop partway through.
for n in range(10, 0, -1):
    print(f"{n}...")
    time.sleep(0.5)

print("Liftoff!")
