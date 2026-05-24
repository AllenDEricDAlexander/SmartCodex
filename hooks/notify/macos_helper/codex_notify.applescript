on valueAfterFlag(argv, flagName)
    repeat with indexValue from 1 to count of argv
        if item indexValue of argv is flagName then
            if indexValue < count of argv then
                return item (indexValue + 1) of argv
            end if
        end if
    end repeat
    return ""
end valueAfterFlag

on run argv
    set notificationTitle to my valueAfterFlag(argv, "--title")
    set notificationMessage to my valueAfterFlag(argv, "--message")
    set notificationSubtitle to my valueAfterFlag(argv, "--subtitle")
    set notificationSound to my valueAfterFlag(argv, "--sound")

    if notificationTitle is "" then
        set notificationTitle to "Codex"
    end if
    if notificationMessage is "" then
        set notificationMessage to notificationTitle
    end if

    if notificationSubtitle is not "" and notificationSound is not "" then
        display notification notificationMessage with title notificationTitle subtitle notificationSubtitle sound name notificationSound
    else if notificationSubtitle is not "" then
        display notification notificationMessage with title notificationTitle subtitle notificationSubtitle
    else if notificationSound is not "" then
        display notification notificationMessage with title notificationTitle sound name notificationSound
    else
        display notification notificationMessage with title notificationTitle
    end if
end run
