package main

import (
	"fmt"
	"os"
)

func cmdSelftest(args []string) {
	fmt.Println("benchctl selftest: relay + cgroup + telemetry checks")
	ok := true
	if supported, detail := CgroupSupported(); !supported {
		fmt.Println("cgroup: FAIL", detail)
		ok = false
	} else {
		fmt.Println("cgroup: PASS", detail)
	}
	key, src, err := loadAPIKey("")
	if err != nil {
		fmt.Println("credential: FAIL", err)
		ok = false
	} else {
		fmt.Printf("credential: PASS source=%s fingerprint=%s\n", src, keyFingerprint(key))
	}
	host := captureHostInfo()
	fmt.Printf("host: %d cpus, %.1f GiB RAM, load %.2f\n", host.LogicalCPUs,
		float64(host.MemTotalBytes)/(1<<30), host.Load1)
	if !ok {
		os.Exit(1)
	}
}
