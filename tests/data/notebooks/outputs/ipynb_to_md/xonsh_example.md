---
jupyter:
  kernelspec:
    display_name: Xonsh
    language: xonsh
    name: xonsh
---

```xonsh
len($(curl -L https://xon.sh))
```

```xonsh
for filename in `.*`:
    print(filename)
    du -sh @(filename)
```
