//! Where the text caret is on screen, for the flow window's droplet to follow.
//!
//! Position only: two integers in physical screen pixels, never the text, the window title or the
//! application's name. It only works for applications that use the system caret (classic Win32
//! controls such as Notepad's edit area); modern web or UI-framework applications usually do not
//! expose one, and then this returns `None` and the droplet keeps its slow path instead.
//! Nothing is stored or sent anywhere.

/// The caret's bottom-left corner in physical screen pixels, or `None` when there is no system
/// caret to follow.
#[cfg(windows)]
pub fn position() -> Option<(i32, i32)> {
    use windows_sys::Win32::Foundation::POINT;
    use windows_sys::Win32::Graphics::Gdi::ClientToScreen;
    use windows_sys::Win32::UI::WindowsAndMessaging::{
        GetForegroundWindow, GetGUIThreadInfo, GetWindowThreadProcessId, GUITHREADINFO,
    };

    // SAFETY: plain Win32 queries with a zeroed, correctly sized out-structure; no pointers are
    // kept and nothing is written outside `info` and `point`.
    unsafe {
        let foreground = GetForegroundWindow();
        if foreground.is_null() {
            return None;
        }
        let thread = GetWindowThreadProcessId(foreground, std::ptr::null_mut());
        if thread == 0 {
            return None;
        }
        let mut info: GUITHREADINFO = std::mem::zeroed();
        info.cbSize = std::mem::size_of::<GUITHREADINFO>() as u32;
        if GetGUIThreadInfo(thread, &mut info) == 0 || info.hwndCaret.is_null() {
            return None;
        }
        let mut point = POINT {
            x: info.rcCaret.left,
            y: info.rcCaret.bottom,
        };
        if ClientToScreen(info.hwndCaret, &mut point) == 0 {
            return None;
        }
        Some((point.x, point.y))
    }
}

#[cfg(not(windows))]
pub fn position() -> Option<(i32, i32)> {
    None
}

#[cfg(test)]
mod tests {
    #[test]
    fn a_missing_caret_is_none_not_an_error() {
        // Whatever window has the focus while tests run, asking never panics.
        let _ = super::position();
    }
}
