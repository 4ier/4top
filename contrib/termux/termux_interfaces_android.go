//go:build android

// Interface discovery for tailscaled running inside an unprivileged Android app
// such as Termux, where the Tailscale Android app's Java-side getter is absent.
//
// Android denies RTM_GETLINK and bind() on NETLINK_ROUTE to apps, which breaks
// net.Interfaces. An unbound RTM_GETADDR dump is still permitted, and the
// ioctls SIOCGIFNAME/SIOCGIFFLAGS/SIOCGIFMTU fill in what a link dump would.

package main

import (
	"encoding/binary"
	"fmt"
	"net"
	"sort"

	"golang.org/x/sys/unix"
	"tailscale.com/net/netmon"
)

func init() {
	netmon.RegisterInterfaceGetter(termuxInterfaces)
}

func termuxInterfaces() ([]netmon.Interface, error) {
	msgs, err := routeDump(unix.RTM_GETADDR)
	if err != nil {
		return nil, err
	}
	addrs := map[int][]net.Addr{}
	for _, m := range msgs {
		if m.Header.Type != unix.RTM_NEWADDR || len(m.Data) < unix.SizeofIfAddrmsg {
			continue
		}
		family, prefix := m.Data[0], int(m.Data[1])
		index := int(binary.NativeEndian.Uint32(m.Data[4:8]))
		attrs, err := parseAttrs(m.Data[unix.SizeofIfAddrmsg:])
		if err != nil {
			continue
		}
		// IFA_LOCAL is this host's address on point-to-point links; IFA_ADDRESS
		// is the peer there, and the same as IFA_LOCAL everywhere else.
		raw := attrs[unix.IFA_LOCAL]
		if raw == nil {
			raw = attrs[unix.IFA_ADDRESS]
		}
		bits := 32
		if family == unix.AF_INET6 {
			bits = 128
		}
		if raw == nil || len(raw)*8 != bits {
			continue
		}
		ip := net.IP(append([]byte(nil), raw...))
		addrs[index] = append(addrs[index], &net.IPNet{IP: ip, Mask: net.CIDRMask(prefix, bits)})
	}

	fd, err := unix.Socket(unix.AF_INET, unix.SOCK_DGRAM|unix.SOCK_CLOEXEC, 0)
	if err != nil {
		return nil, err
	}
	defer unix.Close(fd)

	var ret []netmon.Interface
	for index, list := range addrs {
		ifc, err := linkByIndex(fd, index)
		if err != nil {
			continue
		}
		ret = append(ret, netmon.Interface{Interface: ifc, AltAddrs: list})
	}
	sort.Slice(ret, func(i, j int) bool { return ret[i].Index < ret[j].Index })
	return ret, nil
}

func linkByIndex(fd, index int) (*net.Interface, error) {
	req, err := unix.NewIfreq("")
	if err != nil {
		return nil, err
	}
	req.SetUint32(uint32(index))
	if err := unix.IoctlIfreq(fd, unix.SIOCGIFNAME, req); err != nil {
		return nil, err
	}
	name := req.Name()
	ifc := &net.Interface{Index: index, Name: name}

	if req, err = unix.NewIfreq(name); err == nil && unix.IoctlIfreq(fd, unix.SIOCGIFFLAGS, req) == nil {
		raw := req.Uint16()
		for bit, flag := range map[uint16]net.Flags{
			unix.IFF_UP: net.FlagUp, unix.IFF_BROADCAST: net.FlagBroadcast,
			unix.IFF_LOOPBACK: net.FlagLoopback, unix.IFF_POINTOPOINT: net.FlagPointToPoint,
			unix.IFF_MULTICAST: net.FlagMulticast, unix.IFF_RUNNING: net.FlagRunning,
		} {
			if raw&bit != 0 {
				ifc.Flags |= flag
			}
		}
	}
	if req, err = unix.NewIfreq(name); err == nil && unix.IoctlIfreq(fd, unix.SIOCGIFMTU, req) == nil {
		ifc.MTU = int(req.Uint32())
	}
	return ifc, nil
}

type routeMessage struct {
	Header unix.NlMsghdr
	Data   []byte
}

// routeDump sends a dump request on an unbound NETLINK_ROUTE socket. Android
// rejects bind(), which is why the standard library's NetlinkRIB fails here.
func routeDump(typ uint16) ([]routeMessage, error) {
	fd, err := unix.Socket(unix.AF_NETLINK, unix.SOCK_RAW|unix.SOCK_CLOEXEC, unix.NETLINK_ROUTE)
	if err != nil {
		return nil, fmt.Errorf("netlink socket: %w", err)
	}
	defer unix.Close(fd)

	req := make([]byte, (unix.NLMSG_HDRLEN+unix.SizeofRtGenmsg+unix.NLMSG_ALIGNTO-1)&^(unix.NLMSG_ALIGNTO-1))
	binary.NativeEndian.PutUint32(req[0:], uint32(len(req)))
	binary.NativeEndian.PutUint16(req[4:], typ)
	binary.NativeEndian.PutUint16(req[6:], unix.NLM_F_DUMP|unix.NLM_F_REQUEST)
	binary.NativeEndian.PutUint32(req[8:], 1)
	req[unix.NLMSG_HDRLEN] = unix.AF_UNSPEC
	if err := unix.Sendto(fd, req, 0, &unix.SockaddrNetlink{Family: unix.AF_NETLINK}); err != nil {
		return nil, fmt.Errorf("netlink dump %d: %w", typ, err)
	}

	var out []routeMessage
	buf := make([]byte, 1<<16)
	for {
		n, _, err := unix.Recvfrom(fd, buf, 0)
		if err != nil {
			return nil, fmt.Errorf("netlink recv: %w", err)
		}
		b := buf[:n]
		for len(b) >= unix.NLMSG_HDRLEN {
			var h unix.NlMsghdr
			h.Len = binary.NativeEndian.Uint32(b[0:])
			h.Type = binary.NativeEndian.Uint16(b[4:])
			if h.Len < unix.NLMSG_HDRLEN || int(h.Len) > len(b) {
				return nil, fmt.Errorf("netlink: malformed message")
			}
			switch h.Type {
			case unix.NLMSG_DONE:
				return out, nil
			case unix.NLMSG_ERROR:
				code := int32(binary.NativeEndian.Uint32(b[unix.NLMSG_HDRLEN:]))
				return nil, fmt.Errorf("netlink dump %d: %w", typ, unix.Errno(-code))
			}
			out = append(out, routeMessage{Header: h, Data: append([]byte(nil), b[unix.NLMSG_HDRLEN:h.Len]...)})
			next := (int(h.Len) + unix.NLMSG_ALIGNTO - 1) &^ (unix.NLMSG_ALIGNTO - 1)
			if next > len(b) {
				break
			}
			b = b[next:]
		}
	}
}

func parseAttrs(b []byte) (map[uint16][]byte, error) {
	attrs := map[uint16][]byte{}
	for len(b) >= unix.SizeofRtAttr {
		l := int(binary.NativeEndian.Uint16(b[0:]))
		t := binary.NativeEndian.Uint16(b[2:])
		if l < unix.SizeofRtAttr || l > len(b) {
			return nil, fmt.Errorf("netlink: malformed attribute")
		}
		attrs[t] = b[unix.SizeofRtAttr:l]
		next := (l + unix.RTA_ALIGNTO - 1) &^ (unix.RTA_ALIGNTO - 1)
		if next > len(b) {
			break
		}
		b = b[next:]
	}
	return attrs, nil
}
